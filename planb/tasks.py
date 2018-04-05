import logging
import re
import time

from dutree import Scanner

from django.core.mail import mail_admins
from django.db import connection
from django.utils import timezone

from django_q.tasks import async

from .models import BOGODATE, BackupRun, HostConfig, bfs

logger = logging.getLogger(__name__)

_yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9*/_.-]*$')


SINGLE_JOB_OPTS = {
    'hook': 'planb.tasks.single_job_done',
    'group': 'Single backup job',
}


# class Request(urllib2.Request):
#     def __init__(self, *args, **kwargs):
#         self._method = kwargs.pop('method', None)
#         urllib2.Request.__init__(self, *args, **kwargs)
#
#     def get_method(self):
#         if self._method is not None:
#             return self._method
#         return urllib2.Request.get_method(self)


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


def async_backup_job(job):
    return async(
        'planb.tasks.spawn_single_backup_job', job.pk,
        q_options=SINGLE_JOB_OPTS)


def single_job_done(task):
    job = HostConfig.objects.get(pk=task.args[0])
    HostConfig.objects.filter(pk=task.args[0]).update(
        queued=False, running=False)

    if task.success:
        logger.info('[%s] Done', job)
    else:
        logger.error('[%s] Failed: %s', job, task.result)


def spawn_single_backup_job(job_id):
    job = HostConfig.objects.get(pk=job_id)

    # The task is delayed, but it has been scheduled/queued.
    logger.info('[%s] Manually requested backup', job)
    if not job.running:
        # Hack so we get success mail. (Only update first_fail if it was
        # unset.)
        HostConfig.objects.filter(pk=job_id, first_fail=None).update(
            first_fail=BOGODATE)

        # Run job. May raise an error. Always restores queued/running.
        unconditional_job_run(job.pk)


def spawn_backup_jobs():
    """
    Schedule all eligible jobs to run soon.
    """
    for job in enum_eligible_jobs():
        async(
            'planb.tasks.conditional_job_run', job.pk,
            q_options=SINGLE_JOB_OPTS)
        logger.info('[%s] Scheduled backup', job)


def enum_eligible_jobs():
    job_qs = (
        HostConfig.objects
        .filter(enabled=True, running=False, queued=False)
        .order_by('last_run'))  # order by last attempt

    for job in job_qs:
        # We have a job_id, lock it. If changed is 0, we did not do a change,
        # ergo we did not lock it. Move along.
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

        # Start the backup.
        logger.info('[%s] Eligible for backup', job)

        yield job


def conditional_job_run(job_id):
    now = timezone.now()
    if 9 <= now.hour < 17:
        job = HostConfig.objects.get(pk=job_id)
        logger.info('[%s] Skipped because of office hours', job)
        # We could retry this, but we don't need to. The jobs are rescheduled
        # every hour, so the next hour we'll arrive here too and do the same
        # time-check.
        # #self.retry(eta=now.replace(hour=17))  # @task(bind=True)
        # Instead, we do this:
        HostConfig.objects.filter(pk=job_id).update(
            queued=False, running=False)
        return

    return unconditional_job_run(job_id)


def unconditional_job_run(job_id):
    job = HostConfig.objects.get(pk=job_id)
    first_fail = job.first_fail

    # Mark it as running.
    HostConfig.objects.filter(pk=job_id).update(running=True)
    t0 = time.time()
    logger.info('[%s] Starting backup', job)

    # Create log.
    run = BackupRun.objects.create(hostconfig_id=job_id)
    try:
        # Rsync job.
        job.run()
        # Dutree job.
        path = bfs.data_dir_get(
            job.dest_pool, str(job.hostgroup), job.friendly_name)
        dutree = Scanner(path).scan()

        # Close the DB connection because it may be stale.
        connection.close()

        # Yay, we're done.
        job.refresh_from_db()

        # Store success on the run job.
        total_size_mb = job.backup_size_mb  # bleh.. get it from here..
        snapshot_size_mb = (dutree.size() + 524288) // 1048576
        snapshot_size_yaml = '\n'.join(
            '{}: {}'.format(
                yaml_safe_str(i.name()[len(path):]), yaml_digits(i.size()))
            for i in dutree.get_leaves())
        BackupRun.objects.filter(pk=run.pk).update(
            duration=(time.time() - t0),
            success=True,
            total_size_mb=total_size_mb,
            snapshot_size_mb=snapshot_size_mb,
            snapshot_size_listing=snapshot_size_yaml)
        run.refresh_from_db()

        # Cache values on the hostconfig.
        now = timezone.now()
        HostConfig.objects.filter(pk=job_id).update(
            last_ok=now,                            # success
            last_run=now,                           # now
            first_fail=None,                        # no failure
            complete_duration=run.duration,         # "runtime"
            backup_size_mb=run.total_size_mb)       # "disk usage"

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
        HostConfig.objects.filter(pk=job_id).update(
            last_run=now)    # don't overwrite last_ok
        HostConfig.objects.filter(pk=job_id, first_fail=None).update(
            first_fail=now)  # overwrite first_fail only if unset

        # Don't re-raise exception. We'll handle it.
        # As far as the workers are concerned, this job is done.
        # #raise

    else:
        logger.info('[%s] Completed successfully', job)
