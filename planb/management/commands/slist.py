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

        # For the leaf/parent checks to be effective, we need the database
        # config immediately before excluding anything.
        datasets = Datasets(datasets)
        datasets.load_database_config()
        datasets.keep_only_leaves()

        for exclude in set(options['exclude']):
            datasets = Datasets([
                ds for ds in datasets if not fnmatch(ds.name, exclude)])

        if options['stale']:
            datasets = Datasets([
                ds for ds in datasets if not ds.exists_in_database])

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
                if hostgroup == '(nogroup)':
                    # XXX: add temporary warning regarding cleanup
                    ret.append(
                        '; (when purging, do not forget to remove '
                        'encryption keys from zfskeys dir)')
                    ret.append('; (see planb-zfskeys-check contrib tool)')

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
