from django.core.management.base import BaseCommand

from planb.models import HostConfig
from planb.tasks import async_backup_job


class Command(BaseCommand):
    help = 'Clones the HostConfig of ID n'

    def add_arguments(self, parser):
        parser.add_argument('hostconfig_id', type=int)
        parser.add_argument('friendly_name')
        parser.add_argument('host')

    def handle(self, *args, **options):
        template = HostConfig.objects.get(pk=options['hostconfig_id'])
        copy = template.clone(
            friendly_name=options['friendly_name'],
            host=options['host'])
        self.stdout.write(self.style.SUCCESS('Cloned {} to {}'.format(
            template, copy)))

        # Spawn a single run.
        HostConfig.objects.filter(pk=copy.pk).update(queued=True)
        task_id = async_backup_job(copy)
        self.stdout.write(self.style.SUCCESS('Enqueued {} job as {}'.format(
            copy, task_id)))
