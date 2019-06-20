import logging
import os
import re
import redis
import time

from dutree import Scanner

from django.conf import settings
from django.core.mail import mail_admins
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from django_q.tasks import async

from .models import BOGODATE, BackupRun, Fileset

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    getproctitle = None
    setproctitle = (lambda x: None)

logger = logging.getLogger(__name__)

_yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9*/_.-]*$')


SINGLE_JOB_OPTS = {
    'hook': 'planb.core.tasks.finalize_run',
    'group': 'Single backup job',
}


class FairButUnsafeRedisLock:
    """
    It's a fair lock -- first come first serve -- but if the redis DB is
    flushed, we don't mind handing out a second simultaneous access.
    """
    @staticmethod
    def get_connection():
        return redis.StrictRedis(**settings.Q_CLUSTER['redis'])

    def __init__(self, redisconn, key, uniqueid):
        self._conn = redisconn
        self._key = key
        self._value = str(uniqueid).encode('utf-8')
        self._needs_pop = False

    def wait_for_turn(self):
        t0 = time.time()
        self._enqueue()
        while self._peek() != self._value:
            time.sleep(1)
        return (time.time() - t0)

    def i_am_done(self):
        self._dequeue()

    def __enter__(self):
        return self.wait_for_turn()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.i_am_done()
        except Exception:
            pass

    def _enqueue(self):
        assert not self._needs_pop
        self._conn.rpush(self._key, self._value)
        self._needs_pop = True

    def _peek(self):
        assert self._needs_pop
        ret = self._conn.lindex(self._key, 0)
        if ret is None:
            # Did someone flush the redis DB? Reschedule self.
            self._needs_pop = False
            self._enqueue()
        return ret

    def _dequeue(self):
        assert self._needs_pop
        ret = self._conn.lpop(self._key)
        assert ret in (self._value, None)  # allow flushed redis
        self._needs_pop = False


def only_one_dutree_at_a_time(disk_pool):
    return FairButUnsafeRedisLock(
        FairButUnsafeRedisLock.get_connection(),
        'dutree:{}'.format(disk_pool),
        os.getpid())


def yaml_safe_str(value):
    if _yaml_safe_re.match(value):
        return value
    return '"{}"'.format(
        value.replace('\\', '\\\\').replace('"', '\\"'))


def yaml_digits(value):
    # Not really yaml, but we'll make them both readable and precise.
    # 1234567 => 1,234,567
    value = str(value)
    assert '.' not in value
    off = len(value) % 3
    return ','.join(
        value[max(0, i + off - 3):(i + off)]
        for i in range(0, len(value) + 1, 3)).lstrip(',')


# Sync called task; spawns async.
def async_backup_job(fileset):
    """
    Schedule the specified fileset to backup at once.
    """
    return async(
        'planb.tasks.manual_run', fileset.pk,
        q_options=SINGLE_JOB_OPTS)


# Sync called task; spawns async.
def spawn_backup_jobs():
    """
    Schedule all eligible filesets to backup soon.
    """
    JobSpawner().spawn_eligible()


# Async called task:
def conditional_run(fileset_id):
    FilesetRunner(fileset_id).conditional_run()


# Async called task:
def manual_run(fileset_id):
    FilesetRunner(fileset_id).manual_run()


# Async called task:
def unconditional_run(fileset_id):
    FilesetRunner(fileset_id).unconditional_run()


# Async called task:
def finalize_run(task):
    fileset_id = task.args[0]
    FilesetRunner(fileset_id).finalize_run(task.success, task.result)


class JobSpawner:
    def spawn_eligible(self):
        for fileset in self._enum_eligible_filesets():
            async(
                'planb.tasks.conditional_run', fileset.pk,
                q_options=SINGLE_JOB_OPTS)
            logger.info('[%s] Scheduled backup', fileset)

    def _enum_eligible_filesets(self):
        fileset_qs = (
            Fileset.objects
            .filter(is_enabled=True, is_running=False, is_queued=False)
            .order_by('last_run'))  # order by last attempt

        for fileset in fileset_qs:
            # We have a fileset_id, lock it. If changed is 0, we did not do
            # a change, ergo we did not lock it. Move along.
            changed = Fileset.objects.filter(
                pk=fileset.pk, is_queued=False).update(is_queued=True)
            if not changed:
                logger.info('[%s] Skipped because already locked', fileset)
                continue

            # Check if the daily exists already.
            if not fileset.should_backup():
                # Unlock.
                Fileset.objects.filter(
                    pk=fileset.pk, is_queued=True).update(is_queued=False)
                continue

            # Check if we failed recently.
            if fileset.first_fail and (
                    (timezone.now() - fileset.last_run).total_seconds() <
                    3600):
                # Unlock.
                Fileset.objects.filter(
                    pk=fileset.pk, is_queued=True).update(is_queued=False)
                logger.info('[%s] Skipped because of recent failure', fileset)
                continue

            # This one is good. May start the backup.
            logger.info('[%s] Eligible for backup', fileset)
            yield fileset


class FilesetRunner:
    def __init__(self, fileset_id):
        self._fileset_id = fileset_id

    def get_average_duration(self):
        # Take average of last 10 runs.
        durations = (
            BackupRun.objects
            .filter(fileset_id=self._fileset_id, success=True)
            .order_by('-id').values_list('duration', flat=True))[0:10]
        if not durations:
            return 0  # impossible.. we should have backupruns if we call this
        return sum(durations) // len(durations)

    def get_dutree_listing(self, fileset, dataset):
        if fileset.do_snapshot_size_listing:
            # Only one dutree at a time.
            logger.info('[%s] Waiting for dutree lock', fileset)
            setproctitle('[backing up %d: %s]: dutree (waiting for lock)' % (
                fileset.pk, fileset.friendly_name))

            with only_one_dutree_at_a_time(fileset.dest_pool) as \
                    waited_seconds:
                # Yes, got lock.
                logger.info('[%s] Got dutree lock', fileset)
                setproctitle('[backing up %d: %s]: dutree' % (
                    fileset.pk, fileset.friendly_name))
                path = dataset.get_data_path()
                dutree = Scanner(path).scan(use_apparent_size=False)

                # Get snapshot size and tree.
                snapshot_size_mb = (
                    dutree.use_size() + 524288) >> 20  # bytes to MiB
                snapshot_size_yaml = '\n'.join(
                    '{}: {}'.format(
                        yaml_safe_str(i.name()[len(path):]),
                        yaml_digits(i.use_size()))
                    for i in dutree.get_leaves())
        else:
            # Set the values to empty.
            waited_seconds = 0
            snapshot_size_mb = 0  # FIXME: get from elsewhere?
            snapshot_size_yaml = 'summary_disabled: 0'

        return {
            'lock_wait_time': waited_seconds,
            'snapshot_size_mb': snapshot_size_mb,
            'snapshot_size_yaml': snapshot_size_yaml}

    def conditional_run(self):
        now = timezone.now()
        if 9 <= now.hour < 17:
            fileset = Fileset.objects.get(pk=self._fileset_id)
            logger.info('[%s] Skipped because of office hours', fileset)
            # We could retry this, but we don't need to. The jobs are
            # rescheduled every hour, so the next hour we'll arrive here
            # too and do the same time-check.
            # #self.retry(eta=now.replace(hour=17))  # @task(bind=True)
            # Instead, we do this:
            Fileset.objects.filter(pk=fileset.pk).update(
                is_queued=False, is_running=False)
            return

        return self.unconditional_run()

    def manual_run(self):
        fileset = Fileset.objects.get(pk=self._fileset_id)

        # The task is delayed, but it has been scheduled/queued.
        logger.info('[%s] Manually requested backup', fileset)
        if not fileset.is_running:
            # Hack so we get success mail. (Only update first_fail if it
            # was unset.)
            Fileset.objects.filter(pk=fileset.pk, first_fail=None).update(
                first_fail=BOGODATE)

            # Run fileset. May raise an error. Always restores queued/running.
            self.unconditional_run()

    def unconditional_run(self):
        fileset = Fileset.objects.get(pk=self._fileset_id)
        first_fail = fileset.first_fail
        if getproctitle:
            oldproctitle = getproctitle()

        # Mark it as running.
        Fileset.objects.filter(pk=fileset.pk).update(is_running=True)
        t0 = time.time()
        logger.info('[%s] Starting backup', fileset)

        # Lock and open dataset for work.
        dataset = fileset.get_dataset()
        dataset.begin_work()
        try:
            # Create log.
            run = BackupRun.objects.create(fileset_id=fileset.pk)

            # Rsync fileset.
            setproctitle('[backing up %d: %s]: rsync' % (
                fileset.pk, fileset.friendly_name))
            fileset.get_transport().run_transport()

            # Get snapshot_size_listing.
            dutree = self.get_dutree_listing(fileset, dataset)

            # Update snapshots.
            setproctitle('[backing up %d: %s]: snapshots' % (
                fileset.pk, fileset.friendly_name))
            fileset.snapshot_rotate()
            fileset.snapshot_create()

            # Close the DB connection because it may be stale.
            connection.close()

            # Yay, we're done.
            fileset.refresh_from_db()

            # Get total size.
            total_size = dataset.get_used_size()
            total_size_mb = (total_size + 524288) >> 20  # bytes to MiB

            # Store run info.
            BackupRun.objects.filter(pk=run.pk).update(
                duration=(time.time() - t0 - dutree['lock_wait_time']),
                success=True,
                total_size_mb=total_size_mb,
                snapshot_size_mb=dutree['snapshot_size_mb'],
                snapshot_size_listing=dutree['snapshot_size_yaml'])

            # Cache values on the fileset.
            now = timezone.now()
            Fileset.objects.filter(pk=fileset.pk).update(
                last_ok=now,                        # success
                last_run=now,                       # now
                first_fail=None,                    # no failure
                average_duration=self.get_average_duration(),
                total_size_mb=total_size_mb)       # "disk usage"

            # Mail if failed recently.
            if first_fail:  # last job was not okay
                if first_fail == BOGODATE:
                    msg = 'Backing up {} was a success.\n'.format(fileset)
                else:
                    msg = (
                        'Backing up {} which was failing since {}.\n\n'
                        'Now all is well again.\n'.format(fileset, first_fail))
                mail_admins('OK: Backup success of {}'.format(fileset), msg)

        except Exception as e:
            if True:  # isinstance(e, DigestableError)
                # Raise log exception with traceback. We could pass it along
                # for Django-Q but it logs errors instead of exceptions and
                # then we don't have any useful tracebacks.
                logger.exception('Backing up %s failed', fileset)
            else:
                # If the error is digestable, log an error without mail and
                # have someone run a daily mail about this instead.
                pass

            # Close the DB connection because it may be stale.
            connection.close()

            # Store failure on the run fileset.
            BackupRun.objects.filter(pk=run.pk).update(
                duration=(time.time() - t0), success=False, error_text=str(e))

            # Cache values on the fileset.
            now = timezone.now()
            Fileset.objects.filter(pk=fileset.pk).update(
                last_run=now)    # don't overwrite last_ok
            (Fileset.objects.filter(pk=fileset.pk)
             .filter(Q(first_fail=None) | Q(first_fail=BOGODATE))
             .update(first_fail=now))  # overwrite first_fail only if unset

            # Don't re-raise exception. We'll handle it.
            # As far as the workers are concerned, this job is done.
            # #raise

        else:
            logger.info('[%s] Completed successfully', fileset)

        finally:
            dataset.end_work()

            if getproctitle:
                setproctitle(oldproctitle)

    def finalize_run(self, success, resultset):
        # Set the queued/running to False when we're done.
        fileset = Fileset.objects.get(pk=self._fileset_id)
        Fileset.objects.filter(pk=fileset.pk).update(
            is_queued=False, is_running=False)

        # This is never not success, as we handled all cases in the
        # unconditional_run, we hope.
        if not success:
            # This should mail someone.
            logger.error('[%s] Job run failure: %r', fileset, resultset)
            fileset.signal_done(success=False)
            return

        logger.info('[%s] Done', fileset)
        fileset.signal_done(success=True)
