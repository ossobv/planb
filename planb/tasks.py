import logging
import re
import signal
import time

from dutree import Scanner

from django.conf import settings
from django.core.mail import mail_admins
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from django_q.brokers import get_broker
from django_q.conf import Conf as DQConf
from django_q.tasks import async_task
from yaml import safe_dump, safe_load

from .models import BOGODATE, BackupRun, Fileset, FilesetLock

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    getproctitle = None
    setproctitle = (lambda x: None)

logger = logging.getLogger(__name__)

_yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9*/_.-]*$')

'''
Backups are run asynchronous with entry points:
 - planb.tasks.conditional_run
 - planb.tasks.manual_run

manual_run:
 - Start a unconditional_run if none are running.

conditional_run:
 - Check the schedule and start a unconditional_run if needed.

unconditional_run:
 - Mount the dataset if needed.
 - Run the transport to transfer the backup.
 - Store administrative data on the FileSet and BackupRun.
 - Start the task dutree_run if needed.
 - Email backup status to admins.
 - finalize_run is invoked as a hook after unconditional_run completes.

finalize_run:
 - sends the planb.signals.backup_done signal.
'''


if not DQConf.SIGNAL_NAMES:
    # Bug in django_q.conf that imports signal function instead of signal
    # module and then tries to get the global SIG* names.
    DQConf.SIGNAL_NAMES = dict(
        (getattr(signal, n), n)
        for n in dir(signal)
        if n.startswith("SIG") and "_" not in n
    )


class handle_exit_signals:
    @staticmethod
    def signal_as_systemexit(signum, frame):
        """
        By default, SIGTERM does not cause an exception and will therefore not
        trickle up through contexthandlers. We want clean exits on SIGTERM, so
        we'll raise the exception ourselves.
        """
        # signal.signal(signum, signal.SIG_IGN)  # ignore further invocations?
        raise SystemExit(128 | signum)

    all_signals = (
        signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM)

    def __enter__(self):
        # Make sure we exit using an exception (and thus the context handler).
        self._prev_handlers = []
        for signum in self.all_signals:
            self._prev_handlers.append(
                signal.signal(signum, self.signal_as_systemexit))

    def __exit__(self, type, value, traceback):
        # Reset the original handler.
        for idx, signum in enumerate(self.all_signals):
            signal.signal(signum, self._prev_handlers[idx])


def systemd_unescape(value):
    r"abc\x2ddef-ghi -> abc-def/ghi"
    parts = value.split('-')
    return '/'.join(i.encode('ascii').decode('unicode_escape') for i in parts)


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
def async_backup_job(fileset, custom_snapname=None):
    """
    Schedule the specified fileset to backup at once.
    """
    return async_task(
        'planb.tasks.manual_run', fileset.pk, custom_snapname,
        broker=get_broker(settings.Q_MAIN_QUEUE),
        q_options={'hook': 'planb.tasks.finalize_run'})


# Sync called task; spawns async.
def async_rename_job(fileset, new_namespace, new_name):
    """
    Spawn a task to rename the fileset.
    """
    new_dataset_name = fileset.storage.name_dataset(new_namespace, new_name)
    return async_task(
        'planb.tasks.rename_run', fileset.pk, fileset.dataset_name,
        new_dataset_name, broker=get_broker(settings.Q_MAIN_QUEUE))


# Sync called task; spawns async.
def spawn_backup_jobs():
    """
    Schedule all eligible filesets to backup soon.
    """
    JobSpawner().spawn_eligible()


# Async called task:
def conditional_run(fileset_id):
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.conditional_run()


# Async called task:
def manual_run(fileset_id, custom_snapname):
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.manual_run(custom_snapname)


# Async called task:
def unconditional_run(fileset_id):
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.unconditional_run()


# Async called task:
def dutree_run(fileset_id, run_id):
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.dutree_run(run_id)


# Async called task:
def rename_run(fileset_id, old_dataset_name, new_dataset_name):
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.rename_run(old_dataset_name, new_dataset_name)


# Async called task:
def finalize_run(task):
    fileset_id = task.args[0]
    with handle_exit_signals(), FilesetRunner(fileset_id) as runner:
        runner.finalize_run(task.success, task.result)


class JobSpawner:
    def spawn_eligible(self):
        enabled_filesets = Fileset.objects.filter(is_enabled=True).count()
        # Each day the system spawns fileset * 2 (conditional_run + dutree_run)
        # and 24 spawn_backup_jobs tasks. Add a margin of 26 to allow for an
        # early warning.
        # If the amount exceeds the save limit we may lose the backup_done
        # signals for a number of backups.
        if enabled_filesets * 2 + 50 > DQConf.SAVE_LIMIT:
            logger.warning('Enabled filesets (%d) are close to exceeding the '
                           'minimum django-q save limit (%d). You should '
                           'increase the save limit', enabled_filesets,
                           DQConf.SAVE_LIMIT)

        for fileset in self._enum_eligible_filesets():
            async_task(
                'planb.tasks.conditional_run', fileset.pk,
                broker=get_broker(settings.Q_MAIN_QUEUE),
                q_options={'hook': 'planb.tasks.finalize_run'})
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
                    (timezone.now() - fileset.last_run).total_seconds()
                    < 3600):
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
        self._fileset_lock = FilesetLock(fileset_id)

    def __enter__(self):
        # Use blocking so the contained code is only executed when the lock is
        # acquired.
        self._fileset_lock.acquire(blocking=True)
        return self

    def __exit__(self, type, value, traceback):
        self._fileset_lock.release()

    def get_average_duration(self):
        # Take average of last 10 runs.
        durations = (
            BackupRun.objects
            .filter(fileset_id=self._fileset_id, success=True)
            .order_by('-id').values_list('duration', flat=True))[0:10]
        if not durations:
            return 0  # impossible.. we should have backupruns if we call this
        return sum(durations) // len(durations)

    def conditional_run(self):
        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')
        fileset = Fileset.objects.get(pk=self._fileset_id)
        if fileset.is_in_blacklist_hours:
            logger.info(
                '[%s] Skipped because of blacklist hours: %s',
                fileset, fileset.get_blacklist_hours())
            # We could retry this, but we don't need to. The jobs are
            # rescheduled every hour, so the next hour we'll arrive here
            # too and do the same time-check.
            # #self.retry(eta=now.replace(hour=17))  # @task(bind=True)
            # Instead, we do this:
            Fileset.objects.filter(pk=fileset.pk).update(
                is_queued=False, is_running=False)
            return

        return self.unconditional_run()

    def manual_run(self, custom_snapname):
        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')
        fileset = Fileset.objects.get(pk=self._fileset_id)

        # The task is delayed, but it has been scheduled/queued.
        logger.info('[%s] Manually requested backup%s', fileset, (
            ' (-> {})'.format(custom_snapname) if custom_snapname else ''))
        if not fileset.is_running:
            # Hack so we get success mail. (Only update first_fail if it
            # was unset.)
            Fileset.objects.filter(pk=fileset.pk, first_fail=None).update(
                first_fail=BOGODATE)

            # Run fileset. May raise an error. Always restores queued/running.
            self.unconditional_run(custom_snapname=custom_snapname)

    def unconditional_run(self, custom_snapname=None):
        assert (custom_snapname or custom_snapname is None) and (
            custom_snapname != 'planb'), custom_snapname

        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')
        fileset = Fileset.objects.get(pk=self._fileset_id)
        if getproctitle:
            oldproctitle = getproctitle()

        # Mark it as running.
        Fileset.objects.filter(pk=fileset.pk).update(is_running=True)
        t0 = time.time()
        logger.info('[%s] Starting backup', fileset)
        run = BackupRun.objects.create(
            fileset_id=fileset.pk, snapshot_name=(custom_snapname or ''))
        dataset = fileset.get_dataset()
        try:
            # Lock and open dataset for work.
            with dataset.workon():
                self._unconditional_run_work(fileset, dataset, run, t0)

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
            return

        else:
            logger.info('[%s] Completed successfully', fileset)

        finally:
            if getproctitle:
                setproctitle(oldproctitle)

        # And now, spawn the dutree listing when all previous work is done and
        # finalized.
        if fileset.do_snapshot_size_listing:
            async_task(
                'planb.tasks.dutree_run', fileset.pk, run.pk,
                broker=get_broker(settings.Q_DUTREE_QUEUE))

    def _unconditional_run_work(self, fileset, dataset, run, t0):
        # Set title, create log, get transport config.
        setproctitle('[backing up %d: %s]: transporting' % (
            fileset.pk, fileset.friendly_name))
        first_fail = fileset.first_fail
        transport = fileset.get_transport()
        # Fixate the snapshot name if the transport creates snapshots.
        planned_snapshot = fileset.get_next_snapshot_name()
        transport.run_transport()

        # Flush dataset properties so we get fresh ones. Do before
        # has_child_datasets().
        dataset.flush()

        # Update snapshots.
        setproctitle('[backing up %d: %s]: snapshots' % (
            fileset.pk, fileset.friendly_name))

        # We rotate *before* creating new snapshots: the new rotate code might
        # otherwise deem the latest backup to be superfluous and immediately
        # remove it. That would be a shame.
        if not transport.can_rotate_snapshot:
            fileset.snapshot_rotate()

        # Extra custom snapshots?
        if run.snapshot_name:
            # Make an extra snapshot, which we'll keep because it has a
            # non-standard prefix.
            # XXX: This probably/possibly conflicts with can_create_snapshot
            # (remotely) created snapshots? Test this..
            fileset.snapshot_create(run.snapshot_name)

        # Regular snapshots?
        if transport.can_create_snapshot:
            snapshot = planned_snapshot  # created by the transport
        else:
            snapshot = fileset.snapshot_create()
        snapshot = run.snapshot_name or snapshot  # use custom name, if avail

        # Close the DB connection because it may be stale.
        connection.close()

        # Yay, we're done.
        fileset.refresh_from_db()

        # Get total size, snapshot size and listing.
        total_size = dataset.get_used_size()
        total_size_mb = (total_size + 524288) >> 20  # bytes to MiB
        snapshot_size = dataset.get_referenced_size()
        snapshot_size_mb = (snapshot_size + 524288) >> 20
        if fileset.do_snapshot_size_listing:
            snapshot_size_listing = 'summary_pending: 0'
        else:
            snapshot_size_listing = 'summary_disabled: 0'
        # XXX Include transport export in attributes.
        attributes = safe_dump(dict(
            snapshot=snapshot,
            do_snapshot_size_listing=fileset.do_snapshot_size_listing),
            default_flow_style=False)

        # Store run info.
        BackupRun.objects.filter(pk=run.pk).update(
            attributes=attributes,
            duration=(time.time() - t0),
            success=True,
            total_size_mb=total_size_mb,
            snapshot_size_mb=snapshot_size_mb,
            snapshot_size_listing=snapshot_size_listing)

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
                    'Now all is well again.\n'.format(
                        fileset, first_fail))
            mail_admins(
                'OK: Backup success of {}'.format(fileset), msg)

    def dutree_run(self, run_id):
        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')
        fileset = Fileset.objects.get(pk=self._fileset_id)
        logger.info('[%s] Starting dutree scan', fileset)
        run = BackupRun.objects.get(pk=run_id)
        assert run.fileset_id == fileset.id

        if getproctitle:
            oldproctitle = getproctitle()

        # Retrieve the name of the snapshot created the backup run.
        attributes = safe_load(run.attributes)
        try:
            snapshot = attributes['snapshot']
        except KeyError:
            # Old backupruns could create multiple snapshots.
            snapshot = attributes['snapshots'][0]

        # Lock and open dataset for work.
        dataset = fileset.get_dataset()
        try:
            if dataset.has_child_datasets():
                self._snapshot_info_from_child_datasets(dataset, snapshot, run)
            else:
                self._snapshot_info_from_dutree(
                    fileset, dataset, snapshot, run)
        finally:
            if getproctitle:
                setproctitle(oldproctitle)

    def _snapshot_info_from_child_datasets(self, dataset, snapshot, run):
        snapshot_size = 524288
        snapshot_size_listing = []
        for subset in dataset.get_child_datasets():
            # Take only last bit from tank/group-host/[remote-tank/remote-file]
            # ("remote-tank/remote-file" is "remote_x2dtank-remote_x2dfile")
            # NOTE: "_x2f" -> "\x2f" which gets unescaped to "/" (slash)
            # NOTE: An underscore without trailing "_" is undefined. Leave it,
            # so we don't get blah_non_cool turning into blah<LF>non<LF>cool.
            subset_name = systemd_unescape(
                subset.name[(len(dataset.name) + 1):].replace('_x', '\\x'))

            size = subset.get_referenced_size(snapshot)
            snapshot_size += size
            snapshot_size_listing.append(
                '{}: {}'.format(
                    yaml_safe_str(subset_name),
                    yaml_digits(size))
            )

        BackupRun.objects.filter(pk=run.pk).update(
            snapshot_size_mb=snapshot_size >> 20,
            snapshot_size_listing='\n'.join(snapshot_size_listing),
        )

    def _snapshot_info_from_dutree(self, fileset, dataset, snapshot, run):
        path = dataset.get_snapshot_path(snapshot)
        try:
            with dataset.workon(path):
                setproctitle('[backing up %d: %s]: dutree' % (
                    fileset.pk, fileset.friendly_name))
                dutree = Scanner(path).scan(use_apparent_size=False)

                # Get snapshot size and tree.
                snapshot_size_mb = (
                    dutree.use_size() + 524288) >> 20  # bytes to MiB
                snapshot_size_yaml = '\n'.join(
                    '{}: {}'.format(
                        yaml_safe_str(i.name()[len(path):]),
                        yaml_digits(i.use_size()))
                    for i in dutree.get_leaves())
                BackupRun.objects.filter(pk=run.pk).update(
                    snapshot_size_mb=snapshot_size_mb,
                    snapshot_size_listing=snapshot_size_yaml,
                )
        except Exception as e:
            logger.exception('[%s] Failed dutree scan', fileset)
            # Append dutree error to error_text, leave success flag as is.
            error_text = (
                BackupRun.objects.filter(pk=run.pk)
                .values_list('error_text', flat=True)[0])
            BackupRun.objects.filter(pk=run.pk).update(
                snapshot_size_listing='summary_error: 0',
                error_text=('{}\n{}'.format(error_text, e).strip())
            )
        else:
            logger.info('[%s] Completed dutree scan', fileset)

    def finalize_run(self, success, resultset):
        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')
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

    def rename_run(self, old_dataset_name, new_dataset_name):
        if not self._fileset_lock.is_acquired():
            raise ValueError('Cannot use fileset without acquiring lock')

        fileset = Fileset.objects.get(pk=self._fileset_id)
        if fileset.dataset_name != old_dataset_name:
            # The fileset dataset name has changed since starting this job.
            logger.warning(
                '[%s] Fileset name to %r cancelled, dataset %r does '
                'not match current %r', fileset, new_dataset_name,
                old_dataset_name, fileset.dataset_name)
            return

        logger.info(
            '[%s] Rename from %r to %r',
            fileset, old_dataset_name, new_dataset_name)
        fileset.rename_dataset(new_dataset_name)
        logger.info('[%s] Rename to %r complete', fileset, new_dataset_name)
