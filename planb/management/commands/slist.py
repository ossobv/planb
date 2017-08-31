from planb.management.base import BaseCommand
from planb.models import bfs
from planb.storage.base import Datasets


def human_bytes(bytes_):
    KB = 1 << 10
    MB = 1 << 20
    GB = 1 << 30
    TB = 1 << 40
    PB = 1 << 50

    if bytes_ < KB:
        return '{} B'.format(bytes_)
    if bytes_ < MB:
        return '{:.1f} KB'.format(bytes_ / KB)
    if bytes_ < GB:
        return '{:.1f} MB'.format(bytes_ / MB)
    if bytes_ < TB:
        return '{:.1f} GB'.format(bytes_ / GB)
    if bytes_ < PB:
        return '{:.1f} TB'.format(bytes_ / TB)
    return '{:.1f} PB'.format(bytes_ / PB)


class Command(BaseCommand):
    help = 'Lists storage entries/datasets'

    def add_arguments(self, parser):
        parser.add_argument('--stale', action='store_true', help=(
            'List stale (unused) storage entries/datasets only'))

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        datasets = bfs.get_datasets()
        datasets.load_hostconfigs()
        if options['stale']:
            datasets = Datasets([i for i in datasets if not i.hostconfig])

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
                        disk_usage=human_bytes(dataset.disk_usage),
                        host=host))
            else:
                ret.append(
                    '{dataset.identifier:54s}  {disk_usage:>8s}  '
                    'id=NONE'.format(
                        dataset=dataset,
                        disk_usage=human_bytes(dataset.disk_usage)))

        if ret:
            ret.append('')

        self.stdout.write('\n'.join(ret) + '\n')
