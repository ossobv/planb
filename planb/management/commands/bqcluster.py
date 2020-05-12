import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from django_q.brokers import get_broker
from django_q.cluster import Cluster, Sentinel
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
        default_queue = (
            os.environ.get('Q_CLUSTER_QUEUE', settings.Q_MAIN_QUEUE)
            or settings.Q_MAIN_QUEUE)
        parser.add_argument(
            '--queue',
            action='store',
            dest='queue',
            default=default_queue,
            help='Run qcluster for the given queue, defaults to {!r}.'.format(
                default_queue),
        )

    def execute(self, *args, **options):
        self.select_django_q_settings(options['queue'])
        super().execute(*args, **options)

    def select_django_q_settings(self, queue):
        """
        Django Q doesn't allow us to have separate configs per queue.

        Update the Conf dict manually with our adjusted settings.
        """
        env_queue = os.environ.get('Q_CLUSTER_QUEUE', queue) or queue
        if env_queue != queue:
            raise CommandError(
                'conflicting Q_CLUSTER_QUEUE env/option: {!r} != {!r}'.format(
                    env_queue, queue))

        settings.Q_CLUSTER_QUEUE = queue
        settings_q = settings.Q_CLUSTER
        if queue == settings.Q_DUTREE_QUEUE:
            settings_q['workers'] = Conf.WORKERS = settings.Q_DUTREE_WORKERS
            settings_q['scheduler'] = Conf.SCHEDULER = False

        # Double check that the Sentinel gets the values from our updated Conf
        # class.
        dummy_sentinel = Sentinel(None, None, None, start=False)
        assert dummy_sentinel.pool_size == settings_q['workers']

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            'Starting qcluster for queue {!r}'.format(options['queue'])))
        q = Cluster(get_broker(options['queue']))
        q.start()
        if options.get('run_once', False):
            q.stop()
