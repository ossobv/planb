import logging
import re
import time
from datetime import timedelta

from dutree import Scanner

from django.core.mail import mail_admins
from django.db import connection
from django.utils import timezone

from django_q.tasks import async

from .models import BackupRun, HostConfig, bfs

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
    logger.info('[%s] Requested direct backup', job)
    if not job.running:
        # Hack so we get success mail.
        HostConfig.objects.filter(pk=job_id).update(
            failure_datetime=timezone.now())

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
        .order_by('date_complete'))

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
        if job.failure_datetime and (
                job.failure_datetime + timedelta(hours=1) > timezone.now()):
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
    failed_most_recently_at = job.failure_datetime

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
        HostConfig.objects.filter(pk=job_id).update(
            date_complete=timezone.now(),           # "complete date"
            complete_duration=run.duration,         # "runtime"
            backup_size_mb=run.total_size_mb,       # "disk usage"
            failure_datetime=None)                  # "failure date"

        # Mail if failed recently.
        if failed_most_recently_at:
            mail_admins(
                'Success for backup: {}'.format(job),
                'Backing up {} failed most recently at {}.\n\n'
                'Now all is well again.\n'.format(
                    job, failed_most_recently_at))

    except Exception as e:
        # Raise log exception with traceback. We could pass it along for
        # Django-Q but it logs errors instead of exceptions and then we
        # don't have any useful tracebacks.
        logger.exception(
            '[%s] Failed backup of host: %s', job, job.host)

        # Close the DB connection because it may be stale.
        connection.close()

        # Store failure on the run job.
        BackupRun.objects.filter(pk=run.pk).update(
            duration=(time.time() - t0), success=False, error_text=str(e))

        # Cache values on the hostconfig.
        HostConfig.objects.filter(pk=job_id).update(
            failure_datetime=timezone.now())        # only "failure date"

        # Don't re-raise exception. We'll handle it.
        # As far as the workers are concerned, this job is done.
        # #raise

    else:
        logger.info('[%s] Completed successfully', job)


def check_for_not_backed_up_jobs():
    yesterday = timezone.now() - timedelta(days=2)
    jobs = HostConfig.objects.filter(date_complete__lt=yesterday)
    for job in jobs:
        msg = 'job {} has not run since {}'.format(job, job.date_complete)
        mail_admins('HostConfig {} needs attention'.format(job), msg)
