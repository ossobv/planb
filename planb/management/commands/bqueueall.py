from django.core.management.base import BaseCommand

from planb.models import Fileset
from planb.tasks import async_backup_job


class Command(BaseCommand):
    help = 'Enqueues all Filesets for backup'

    def handle(self, *args, **options):
        qs = Fileset.objects.filter(is_queued=False).order_by('pk')
        for fileset in qs:
            Fileset.objects.filter(pk=fileset.pk).update(is_queued=True)
            task_id = async_backup_job(fileset)
            self.stdout.write(self.style.SUCCESS(
                'Enqueued {} job as {}'.format(fileset, task_id)))
