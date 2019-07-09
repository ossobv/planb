import os

from django.core.management.base import BaseCommand
from django.utils.translation import ugettext as _

from django_q.brokers import get_broker
from django_q.cluster import Cluster
from django_q.conf import Conf


class Command(BaseCommand):
    # Translators: help text for qcluster management command
    help = _("Start Django Q Cluster for a queue.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-once',
            action='store_true',
            dest='run_once',
            default=False,
            help='Run once and then stop.',
        )
        default_queue = os.environ.get('Q_CLUSTER_QUEUE', Conf.PREFIX)
        parser.add_argument(
            '--queue',
            action='store',
            dest='queue',
            default=default_queue,
            help='Run qcluster for the given queue, defaults to {!r}.'.format(
                default_queue),
        )

    def handle(self, *args, **options):
        q = Cluster(get_broker(options['queue']))
        q.start()
        if options.get('run_once', False):
            q.stop()
