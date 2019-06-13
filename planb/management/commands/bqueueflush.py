from django.core.management.base import BaseCommand
from django.db.models import Q

from django_q.brokers import get_broker

from planb.models import Fileset


class Command(BaseCommand):
    help = 'Drops all enqueued tasks'

    def handle(self, *args, **options):
        broker = get_broker()
        broker_queue = broker.queue_size()

        db_queue = (
            Fileset.objects.filter(Q(is_running=True) | Q(is_queued=True))
            .update(is_running=False, is_queued=False))
        broker.purge_queue()

        if broker_queue:
            self.stdout.write(self.style.SUCCESS(
                'Dropped {} jobs from Task queue'.format(broker_queue)))
        if db_queue:
            self.stdout.write(self.style.SUCCESS(
                'Dropped {} jobs from DB queue'.format(db_queue)))
