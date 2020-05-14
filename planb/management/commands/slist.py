from fnmatch import fnmatch

from planb.common import human
from planb.management.base import BaseCommand
from planb.storage import storage_pools
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
        datasets = []
        for storage in storage_pools.values():
            datasets.extend(storage.get_datasets())

        for exclude in set(options['exclude']):
            datasets = [i for i in datasets if not fnmatch(i.name, exclude)]

        datasets = Datasets(datasets)
        datasets.load_database_config()
        if options['stale']:
            datasets = Datasets([
                i for i in datasets if not i.exists_in_database])

        datasets.sort()
        self.dump_list(datasets)

    def dump_list(self, datasets):
        ret = []

        lastgroup = None
        for dataset in datasets:
            fileset = dataset.database_object
            hostgroup = fileset.hostgroup if fileset else '(nogroup)'
            if lastgroup != hostgroup:
                lastgroup = hostgroup
                if ret:
                    ret.append('')
                ret.append('; {}'.format(hostgroup))

            if fileset:
                ret.append(
                    '{dataset.name:54s}  {disk_usage:>8s}  '
                    'id={fileset.id}'.format(
                        dataset=dataset,
                        disk_usage=human.bytes(dataset.disk_usage),
                        fileset=fileset))
            else:
                ret.append(
                    '{dataset.name:54s}  {disk_usage:>8s}  '
                    'id=NONE'.format(
                        dataset=dataset,
                        disk_usage=human.bytes(dataset.disk_usage)))

        if ret:
            ret.extend(['', ''])
            self.stdout.write('\n'.join(ret))
