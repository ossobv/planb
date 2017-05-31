from django.core.management.base import BaseCommand

from planb.models import HostConfig
from planb.tasks import async_backup_job


class Command(BaseCommand):
    help = 'Enqueues all HostConfigs for backup'

    def handle(self, *args, **options):
        qs = HostConfig.objects.filter(queued=False).order_by('pk')
        for hostconfig in qs:
            HostConfig.objects.filter(pk=hostconfig.pk).update(queued=True)
            task_id = async_backup_job(hostconfig)
            self.stdout.write(self.style.SUCCESS(
                'Enqueued {} job as {}'.format(hostconfig, task_id)))
