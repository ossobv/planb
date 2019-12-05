from django.core.management.base import BaseCommand

from planb.models import Fileset


class Command(BaseCommand):
    help = 'Clones the Fileset of ID'

    def add_arguments(self, parser):
        parser.add_argument('fileset_id', type=int)
        parser.add_argument('friendly_name')
        parser.add_argument('host')

    def handle(self, *args, **options):
        template = Fileset.objects.get(pk=options['fileset_id'])
        copy = template.clone(
            friendly_name=options['friendly_name'],
            transport__host=options['host'])
        self.stdout.write(self.style.SUCCESS('Cloned {} to {}'.format(
            template, copy)))
