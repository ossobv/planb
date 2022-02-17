#!/usr/bin/env python3
#
# Usage: planb-double-backup-sources USER@MACHINE
#
# Right now:
# - assuming you're running this as root
# - remote has planb access

from argparse import ArgumentParser
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, flock
from pathlib import Path
from subprocess import check_output

DBDIR = Path('/var/lib/planb/sources')


class DatasetStorage:
    def __init__(self, db):
        self.db = db
        self.datasets = set()

        if db.exists():
            with self.open() as f:
                for dataset in f.readlines():
                    self.datasets.add(dataset.rstrip('\n'))

    @contextmanager
    def open(self, mode='r', **kwargs):
        if mode not in ('r', 'w'):
            raise ValueError(f'Invalid mode {mode}, use r or w')
        with self.db.with_name(self.db.name + '.lock').open('w') as lock_f:
            flock(lock_f, LOCK_EX if mode == 'w' else LOCK_SH)
            try:
                with self.db.open(mode, **kwargs) as f:
                    yield f
            finally:
                flock(lock_f, LOCK_UN)

    def add(self, dataset):
        self.datasets.add(dataset)

    def discard(self, dataset):
        self.datasets.discard(dataset)

    def write(self):
        with self.open('w') as f:
            for dataset in self:
                f.write(f'{dataset}\n')

    def __iter__(self):
        return iter(sorted(self.datasets))


def get_server_datasets(server):
    return check_output(
        ['ssh', server, 'planb', 'blist', '--double'],
        text=True).splitlines()


def parse_args():
    parser = ArgumentParser(
        description='Fetch the double backup dataset list from a remote planb '
                    'server. The datasets are kept in a persistent storage to '
                    'prevent configuration changes from wiping the datasets. '
                    'To permanently remove a dataset use the --discard flag.')
    parser.add_argument(
        'server', metavar='USER@HOST',
        help='The backup server using SSH destination notation.')
    parser.add_argument(
        '-l', '--local', action='store_true',
        help='Show the datasets from the persistent storage.')
    parser.add_argument(
        '-a', '--add', metavar='DATASET', action='extend', nargs='+',
        default=[], help='Add a dataset to the persistent storage.')
    parser.add_argument(
        '-d', '--discard', metavar='DATASET', action='extend', nargs='+',
        default=[], help='Remove a dataset from the persistent storage.')

    return parser.parse_args()


def main():
    if not DBDIR.exists():
        DBDIR.mkdir(parents=True)
    elif not DBDIR.is_dir():
        raise TypeError(f'{DBDIR} should be a directory')

    args = parse_args()
    host = args.server.split('@')[-1]
    storage = DatasetStorage(DBDIR / host)

    mutate = bool(args.add or args.discard)
    if mutate:
        for dataset in args.discard:
            storage.discard(dataset)
        for dataset in args.add:
            storage.add(dataset)
        storage.write()
    elif not args.local:
        for dataset in get_server_datasets(args.server):
            storage.add(dataset)
        storage.write()

    if not mutate:
        for dataset in storage:
            print(dataset)


if __name__ == '__main__':
    main()
