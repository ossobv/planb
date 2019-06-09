import logging
import os.path
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.conf import settings

from planb.common.subprocess2 import CalledProcessError

from .base import OldStyleStorage, Datasets, Dataset, DatasetNotFound

# Check if we can backup (daily)
# backup
# Rotate snapshots
# - daily
# - weekly
# - monthly
# - yearly
# create snapshot
# - daily
# - weekly
# - monthly
# - yearly
# Shoot completed flag into monitoring

logger = logging.getLogger(__name__)

SNAPSHOT_SPECIAL_MAPPING = {
    'weekly': ('weekly', relativedelta(weeks=+1)),
    'monthly': ('monthly', relativedelta(months=+1)),
    'yearly': ('yearly', relativedelta(years=+1))
}


class Zfs(OldStyleStorage):
    name = 'zfs'

    def get_datasets(self):
        output = self._perform_binary_command(('list', '-Hpo', 'name,used'))

        datasets = Datasets()
        for line in output.rstrip().split('\n'):
            dataset_name, used = line.split('\t')

            if any(dataset_name.startswith(ppool + '/')
                   for pname, ppool, ptype in settings.PLANB_STORAGE_POOLS):
                dataset = Dataset(backend=self, identifier=dataset_name)
                dataset.set_disk_usage(int(used))
                datasets.append(dataset)

        return datasets

    def get_dataset(self, rootdir, customer, friendly_name):
        dataset_name = self._args_to_dataset_name(
            rootdir, customer, friendly_name)
        return ZfsDataset(backend=self, identifier=dataset_name)

    def _args_to_dataset_name(self, rootdir, customer, friendly_name):
        return '{}/{}-{}'.format(rootdir, customer, friendly_name)

    def _identifier_to_dataset_name(self, identifier):
        return identifier

    def zfs_get_local_path(self, identifier):
        cmd = ('get', '-Ho', 'value', 'mountpoint', identifier)
        try:
            out = self._perform_binary_command(cmd).rstrip('\r\n')
        except CalledProcessError:
            out = None
        return out

    def zfs_get_used_size(self, identifier):
        dataset_name = self._identifier_to_dataset_name(identifier)
        cmd = (
            'get', '-o', 'value', '-Hp', 'used', dataset_name)
        try:
            out = self._perform_binary_command(cmd)
        except CalledProcessError as e:
            msg = 'Error while calling: %r, %s' % (cmd, e.output.strip())
            logger.warning(msg)
            size = '0'
        else:
            size = out.strip()

        return int(size)

    def zfs_create(self, identifier):
        dataset_name = self._identifier_to_dataset_name(identifier)

        # For multi-slash paths, we may need to create parents as well.
        parts = dataset_name.split('/')
        for idx, last_part in enumerate(parts):
            part = '/'.join(parts[0:(idx + 1)])
            try:
                cmd = ('get', '-o', 'value', '-Hp', 'type', part)
                type_ = self._perform_binary_command(cmd).rstrip('\r\n')
            except CalledProcessError:
                # Does not exist. Create it.
                self._perform_binary_command(('create', part))
            else:
                assert type_ == 'filesystem', (identifier, part, type_)

        # After mount, make it ours. Blegh. Unfortunate side-effect of
        # using sudo for the ZFS create.
        try:
            self.zfs_mount(identifier)
        except CalledProcessError:
            pass  # already mounted (we hope?)
        path = self.zfs_get_local_path(identifier)
        self._perform_sudo_command(('chown', str(os.getuid()), path))

        # Log something.
        logger.info('Created ZFS dataset: %s' % identifier)

    def zfs_mount(self, identifier):
        # Even if we have user-powers on /dev/zfs, we still cannot call
        # all commands.
        # $ /sbin/zfs mount rpool/BACKUP/example-example
        # mount: only root can use "--options" option
        # cannot mount 'rpool/BACKUP/example-example': Invalid argument
        # Might as well use sudo everywhere then.
        dataset_name = self._identifier_to_dataset_name(identifier)
        self._perform_binary_command(('mount', dataset_name))

    def zfs_unmount(self, identifier):
        dataset_name = self._identifier_to_dataset_name(identifier)
        self._perform_binary_command(('unmount', dataset_name))

    # (old style)

    def snapshot_create(self, rootdir, customer, friendly_name, snapname=None):
        dataset_name = self._args_to_dataset_name(
            rootdir, customer, friendly_name)
        snapshot_name = '{}@{}'.format(dataset_name, snapname)
        cmd = ('snapshot', snapshot_name)
        self._perform_binary_command(cmd)
        return snapshot_name

    def snapshot_delete(self, snapshot):
        cmd = ('destroy', snapshot)
        self._perform_binary_command(cmd)

    def snapshots_get(self, rootdir, customer, friendly_name, typ=None):
        dataset_name = self._args_to_dataset_name(
            rootdir, customer, friendly_name)
        cmd = (
            'list', '-r', '-H', '-t', 'snapshot', '-o', 'name', dataset_name)
        try:
            out = self._perform_binary_command(cmd)
        except CalledProcessError as e:
            # planb.common.subprocess2.CalledProcessError:
            # /usr/bin/sudo: "cannot open 'poolX/datasetY': dataset does
            # not exist" (exit 1)
            if b'dataset does not exist' in e.errput:
                raise DatasetNotFound()
            raise

        if not out:
            return []

        snapshots = []
        if typ:
            snapshot_rgx = re.compile(r'.*@{}\-\d+'.format(typ))
        else:
            snapshot_rgx = re.compile(r'^.*@\w+-\d+$')
        for snapshot in out.split('\n'):
            if snapshot_rgx.match(snapshot):
                snapshots.append(snapshot)
        return snapshots

    # Note: We use retention + 1 to calculate if we need to
    # retain the backup, in this situation we won't run into
    # situations where your data already gets cleaned up just
    # because it's older than the relative delta.
    # 1 montly retention:
    # 1 jan: monthly created
    # 1 febr: new monthly created
    # 1 febr: old monthly deleted
    # situation, you have data from yesterday and no monthly data
    def snapshot_retain_daily(self, snapname, retention):
        try:
            dts = re.match(r'\w+-(\d+)', snapname).groups()[0]
        except AttributeError:
            return True  # Keep
        datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
        return datetimestamp > (
            datetime.now() - relativedelta(days=retention+1))

    def snapshot_retain_weekly(self, snapname, retention):
        try:
            dts = re.match(r'\w+-(\d+)', snapname).groups()[0]
        except AttributeError:
            return True  # Keep
        datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
        snapdate = datetime.date(datetimestamp)
        today_a_week_ago = datetime.date(
            datetime.now() - relativedelta(weeks=retention+1))
        return snapdate >= today_a_week_ago

    def snapshot_retain_monthly(self, snapname, retention):
        try:
            dts = re.match(r'\w+-(\d+)', snapname).groups()[0]
        except AttributeError:
            return True  # Keep

        datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
        snapdate = datetime.date(datetimestamp)
        today_a_month_ago = datetime.date(
            datetime.now() - relativedelta(months=retention+1))
        return snapdate >= today_a_month_ago

    def snapshot_retain_yearly(self, snapname, retention):
        try:
            dts = re.match(r'\w+-(\d+)', snapname).groups()[0]
        except AttributeError:
            return True  # Keep
        datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
        snapdate = datetime.date(datetimestamp)
        today_a_year_ago = datetime.date(
            datetime.now() - relativedelta(years=retention+1))
        return snapdate >= today_a_year_ago

    def snapshots_rotate(self, rootdir, customer, friendly_name, **kwargs):
        dataset_name = self._args_to_dataset_name(
            rootdir, customer, friendly_name)
        snapshots = self.snapshots_get(rootdir, customer, friendly_name)
        destroyed = []
        logger.info('snapshots rotation for {}'.format(dataset_name))
        for snapshot in snapshots:
            ds, snapname = snapshot.split('@')
            snaptype, dts = re.match(r'(\w+)-(\d+)', snapname).groups()
            snapshot_retain_func = getattr(self,
                                           'snapshot_retain_%s' % snaptype)
            retention = kwargs.get('%s_retention' % snaptype)
            if not snapshot_retain_func(snapname, retention):
                self.snapshot_delete(snapshot)
                destroyed.append(snapshot)
                logger.info('destroyed: {}, past retention'.format(
                        snapshot, retention))
        return destroyed


class ZfsDataset(Dataset):
    # TODO/FIXME: check these methods and add them as NotImplemented to the
    # base

    def ensure_exists(self):
        # Common case is the unmounted yet existing path. If the mount point
        # exists, everything in it should be fine too.
        if self._backend.zfs_get_local_path(self.identifier):
            return

        # Try creating it. (Creation also mounts it.)
        self._backend.zfs_create(self.identifier)

        # Now it should exist. Create the 'data' subdirectory as well.
        if hasattr(self, '_zfs_data_path'):
            del self._zfs_data_path
        path = self.get_data_path()
        os.makedirs(path, 0o700)

        # Unmount if possible.
        try:
            self._backend.zfs_unmount(self.identifier)
        except CalledProcessError:
            pass

    def begin_work(self):
        path = self.get_data_path()  # mount point
        try:
            self._backend.zfs_mount(self.identifier)  # zfs dataset
        except CalledProcessError:
            # Maybe it was already mounted?
            if not os.path.exists(path):
                raise ValueError('Failed to mount {} => {}'.format(
                    self.identifier, path))

        # Jump into 'data' directory, so it cannot be unmounted while we work.
        os.chdir(path)

    def end_work(self):
        # Leave directory, so it can be unmounted.
        os.chdir('/')
        try:
            self._backend.zfs_unmount(self.identifier)  # zfs dataset
        except CalledProcessError:
            # Ok. This might be because someone else is using it. Ignore.
            pass

        # Note that the mount point directory stays, but it will be
        # empty/unmounted (and owned by root) at this point.

    def get_data_path(self):
        if not hasattr(self, '_zfs_data_path'):
            local_path = self._backend.zfs_get_local_path(self.identifier)
            if not local_path:
                raise ValueError(
                    'path {!r} for {!r} does not exist'.format(
                        local_path, self.identifier))
            self._zfs_data_path = os.path.join(local_path, 'data')
        return self._zfs_data_path

    def get_used_size(self):
        if not hasattr(self, '_zfs_get_used_size'):
            self._zfs_get_used_size = self._backend.zfs_get_used_size(
                self.identifier)
        return self._zfs_get_used_size
