from django.core.management.base import BaseCommand

from planb.models import Fileset
from planb.tasks import async_backup_job


class Command(BaseCommand):
    help = 'Enqueues all Filesets for backup'

    def handle(self, *args, **options):
        qs = Fileset.objects.filter(queued=False).order_by('pk')
        for hostconfig in qs:
            Fileset.objects.filter(pk=hostconfig.pk).update(queued=True)
            task_id = async_backup_job(hostconfig)
            self.stdout.write(self.style.SUCCESS(
                'Enqueued {} job as {}'.format(hostconfig, task_id)))
