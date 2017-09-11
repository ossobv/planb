from fnmatch import fnmatch

from planb.management.base import BaseCommand
from planb.models import bfs
from planb.utils import human
from planb.storage.base import Datasets


class Command(BaseCommand):
    help = 'Lists storage entries/datasets'

    def add_arguments(self, parser):
        parser.add_argument(
            '--stale', action='store_true',
            help='List stale (unused) storage entries/datasets only')
        parser.add_argument(
            '-x', '--exclude', action='append', default=[],
            help='Glob patterns to exclude')

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        datasets = bfs.get_datasets()
        for exclude in set(options['exclude']):
            datasets = Datasets([
                i for i in datasets if not fnmatch(i.identifier, exclude)])

        datasets.load_hostconfigs()
        if options['stale']:
            datasets = Datasets([
                i for i in datasets if not i.hostconfig])

        datasets.sort()
        self.dump_list(datasets)

    def dump_list(self, datasets):
        ret = []

        lastgroup = None
        for dataset in datasets:
            host = dataset.hostconfig
            hostgroup = host.hostgroup if host else '(nogroup)'
            if lastgroup != hostgroup:
                lastgroup = hostgroup
                if ret:
                    ret.append('')
                ret.append('; {}'.format(hostgroup))

            if host:
                ret.append(
                    '{dataset.identifier:54s}  {disk_usage:>8s}  '
                    'id={host.id}'.format(
                        dataset=dataset,
                        disk_usage=human.bytes(dataset.disk_usage),
                        host=host))
            else:
                ret.append(
                    '{dataset.identifier:54s}  {disk_usage:>8s}  '
                    'id=NONE'.format(
                        dataset=dataset,
                        disk_usage=human.bytes(dataset.disk_usage)))

        if ret:
            ret.extend(['', ''])
            self.stdout.write('\n'.join(ret))
