import logging
import re
import time

from dutree import Scanner

from django.core.mail import mail_admins
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from django_q.tasks import async

from .models import BOGODATE, BackupRun, HostConfig, bfs

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    getproctitle = None
    setproctitle = (lambda x: None)

logger = logging.getLogger(__name__)

_yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9*/_.-]*$')


SINGLE_JOB_OPTS = {
    'hook': 'planb.tasks.finalize_job_run',
    'group': 'Single backup job',
}


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
def async_backup_job(job):
    """
    Schedule the specified job to run at once.
    """
    return async(
        'planb.tasks.manual_job_run', job.pk,
        q_options=SINGLE_JOB_OPTS)


# Sync called task; spawns async.
def spawn_backup_jobs():
    """
    Schedule all eligible jobs to run soon.
    """
    JobSpawner().spawn_eligible()


# Async called task:
def conditional_job_run(job_id):
    JobRunner(job_id).conditional_job_run()


# Async called task:
def manual_job_run(job_id):
    JobRunner(job_id).manual_job_run()


# Async called task:
def unconditional_job_run(job_id):
    JobRunner(job_id).unconditional_job_run()


# Async called task:
def finalize_job_run(task):
    job_id = task.args[0]
    JobRunner(job_id).finalize_job_run(task.success, task.result)


class JobSpawner:
    def spawn_eligible(self):
        for job in self._enum_eligible_jobs():
            async(
                'planb.tasks.conditional_job_run', job.pk,
                q_options=SINGLE_JOB_OPTS)
            logger.info('[%s] Scheduled backup', job)

    def _enum_eligible_jobs(self):
        job_qs = (
            HostConfig.objects
            .filter(enabled=True, running=False, queued=False)
            .order_by('last_run'))  # order by last attempt

        for job in job_qs:
            # We have a job_id, lock it. If changed is 0, we did not do
            # a change, ergo we did not lock it. Move along.
            changed = HostConfig.objects.filter(
                pk=job.pk, queued=False).update(queued=True)
            if not changed:
                logger.info('[%s] Skipped because already locked', job)
                continue

            # Check if the daily exists already.
            if not job.can_backup():
                # Unlock.
                HostConfig.objects.filter(
                    pk=job.pk, queued=True).update(queued=False)
                continue

            # Check if we failed recently.
            if job.first_fail and (
                    (timezone.now() - job.last_run).total_seconds() < 3600):
                # Unlock.
                HostConfig.objects.filter(
                    pk=job.pk, queued=True).update(queued=False)
                logger.info('[%s] Skipped because of recent failure', job)
                continue

            # This one is good. May start the backup.
            logger.info('[%s] Eligible for backup', job)
            yield job


class JobRunner:
    def __init__(self, job_id):
        self._job_id = job_id

    def get_average_duration(self):
        # Take average of last 10 runs.
        durations = (
            BackupRun.objects
            .filter(hostconfig_id=self._job_id, success=True)
            .order_by('-id').values_list('duration', flat=True))[0:10]
        if not durations:
            return 0  # impossible.. we should have backupruns if we call this
        return sum(durations) // len(durations)

    def conditional_job_run(self):
        now = timezone.now()
        if 9 <= now.hour < 17:
            job = HostConfig.objects.get(pk=self._job_id)
            logger.info('[%s] Skipped because of office hours', job)
            # We could retry this, but we don't need to. The jobs are
            # rescheduled every hour, so the next hour we'll arrive here
            # too and do the same time-check.
            # #self.retry(eta=now.replace(hour=17))  # @task(bind=True)
            # Instead, we do this:
            HostConfig.objects.filter(pk=job.pk).update(
                queued=False, running=False)
            return

        return self.unconditional_job_run()

    def manual_job_run(self):
        job = HostConfig.objects.get(pk=self._job_id)

        # The task is delayed, but it has been scheduled/queued.
        logger.info('[%s] Manually requested backup', job)
        if not job.running:
            # Hack so we get success mail. (Only update first_fail if it
            # was unset.)
            HostConfig.objects.filter(pk=job.pk, first_fail=None).update(
                first_fail=BOGODATE)

            # Run job. May raise an error. Always restores queued/running.
            self.unconditional_job_run()

    def unconditional_job_run(self):
        job = HostConfig.objects.get(pk=self._job_id)
        first_fail = job.first_fail

        # Mark it as running.
        HostConfig.objects.filter(pk=job.pk).update(running=True)
        t0 = time.time()
        logger.info('[%s] Starting backup', job)

        # State that we're running.
        if getproctitle:
            oldproctitle = getproctitle()

        # Create log.
        run = BackupRun.objects.create(hostconfig_id=job.pk)
        try:
            # Rsync job.
            setproctitle('[backing up %d: %s]: rsync' % (
                job.pk, job.friendly_name))
            job.run_rsync()

            # Dutree job.
            setproctitle('[backing up %d: %s]: dutree' % (
                job.pk, job.friendly_name))
            path = bfs.data_dir_get(
                job.dest_pool, job.hostgroup.name, job.friendly_name)
            dutree = Scanner(path).scan()

            # Update snapshots.
            setproctitle('[backing up %d: %s]: snapshots' % (
                job.pk, job.friendly_name))
            job.snapshot_rotate()
            job.snapshot_create()

            # Close the DB connection because it may be stale.
            connection.close()

            # Yay, we're done.
            job.refresh_from_db()

            # Get total size.
            total_size = bfs.parse_backup_sizes(
                job.dest_pool, job.hostgroup.name, job.friendly_name)
            total_size_mb = total_size >> 10  # bytes to MiB

            # Get snapshot size and tree.
            snapshot_size_mb = (dutree.size() + 524288) // 1048576
            snapshot_size_yaml = '\n'.join(
                '{}: {}'.format(
                    yaml_safe_str(i.name()[len(path):]), yaml_digits(i.size()))
                for i in dutree.get_leaves())

            # Store run info.
            BackupRun.objects.filter(pk=run.pk).update(
                duration=(time.time() - t0),
                success=True,
                total_size_mb=total_size_mb,
                snapshot_size_mb=snapshot_size_mb,
                snapshot_size_listing=snapshot_size_yaml)

            # Cache values on the hostconfig.
            now = timezone.now()
            HostConfig.objects.filter(pk=job.pk).update(
                last_ok=now,                        # success
                last_run=now,                       # now
                first_fail=None,                    # no failure
                average_duration=self.get_average_duration(),
                total_size_mb=total_size_mb)       # "disk usage"

            # Mail if failed recently.
            if first_fail:  # last job was not okay
                if first_fail == BOGODATE:
                    msg = 'Backing up {} was a success.\n'.format(job)
                else:
                    msg = (
                        'Backing up {} which was failing since {}.\n\n'
                        'Now all is well again.\n'.format(job, first_fail))
                mail_admins('OK: Backup success of {}'.format(job), msg)

        except Exception as e:
            if True:  # isinstance(e, DigestableError)
                # Raise log exception with traceback. We could pass it along
                # for Django-Q but it logs errors instead of exceptions and
                # then we don't have any useful tracebacks.
                logger.exception(
                    'Backup failed of %s on %s', job, job.host)
            else:
                # If the error is digestable, log an error without mail and
                # have someone run a daily mail about this instead.
                pass

            # Close the DB connection because it may be stale.
            connection.close()

            # Store failure on the run job.
            BackupRun.objects.filter(pk=run.pk).update(
                duration=(time.time() - t0), success=False, error_text=str(e))

            # Cache values on the hostconfig.
            now = timezone.now()
            HostConfig.objects.filter(pk=job.pk).update(
                last_run=now)    # don't overwrite last_ok
            (HostConfig.objects.filter(pk=job.pk)
             .filter(Q(first_fail=None) | Q(first_fail=BOGODATE))
             .update(first_fail=now))  # overwrite first_fail only if unset

            # Don't re-raise exception. We'll handle it.
            # As far as the workers are concerned, this job is done.
            # #raise

        else:
            logger.info('[%s] Completed successfully', job)

        finally:
            if getproctitle:
                setproctitle(oldproctitle)

    def finalize_job_run(self, success, resultset):
        # Set the queued/running to False when we're done.
        job = HostConfig.objects.get(pk=self._job_id)
        HostConfig.objects.filter(pk=job.pk).update(
            queued=False, running=False)

        # This is never not success, as we handled all cases in the
        # unconditional_job_run, we hope.
        if not success:
            # This should mail someone.
            logger.error('[%s] Job run failure: %r', job, resultset)
            job.signal_done(success=False)
            return

        logger.info('[%s] Done', job)
        job.signal_done(success=True)
