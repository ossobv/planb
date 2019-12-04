from contextlib import contextmanager
import logging
import os.path
import re
import time

from datetime import datetime
from dateutil.relativedelta import relativedelta
from functools import lru_cache

from django.core.exceptions import ImproperlyConfigured

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


class ZfsStorage(OldStyleStorage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.poolname = self.config['POOLNAME']
        # Create LRU cache for this instance of Zfs.
        self.zfs_get_property = lru_cache(maxsize=32)(self.zfs_get_property)

    @classmethod
    def ensure_defaults(cls, config):
        super().ensure_defaults(config)
        if 'POOLNAME' not in config:
            raise ImproperlyConfigured('Zfs storage requires a POOLNAME')

    def get_label(self):
        used = int(self.zfs_get_property(self.poolname, 'used'))
        available = int(self.zfs_get_property(self.poolname, 'available'))

        if used and available:
            pct = '{pct:.0f}%'.format(pct=(100 * (used / (used + available))))
            available = int(available / 1024 / 1024 / 1024)
        else:
            available = pct = '???'

        return '{}, {}G free ({} used)'.format(self.name, available, pct)

    def get_datasets(self):
        output = self._perform_binary_command(('list', '-Hpo', 'name,used'))

        datasets = Datasets()
        for line in output.rstrip().split('\n'):
            dataset_name, used = line.split('\t')

            if dataset_name.startswith(self.poolname + '/'):
                dataset = ZfsDataset(backend=self, name=dataset_name)
                dataset.set_disk_usage(int(used))
                datasets.append(dataset)

        return datasets

    def get_dataset(self, dataset_name):
        return ZfsDataset(backend=self, name=dataset_name)

    def get_dataset_name(self, namespace, name):
        return '{}/{}-{}'.format(self.poolname, namespace, name)

    def zfs_get_local_path(self, dataset_name):
        cmd = ('get', '-Ho', 'value', 'mountpoint', dataset_name)
        try:
            out = self._perform_binary_command(cmd).rstrip('\r\n')
        except CalledProcessError as e:
            msg = 'Error while calling: %r, %s' % (cmd, e.output.strip())
            logging.warning(msg)
            out = None
        return out

    def zfs_get_property(
            self, dataset_name, prop, output='value', snapname=None):
        if snapname is not None:
            dataset_name = '{}@{}'.format(dataset_name, snapname)
        cmd = (
            'get', '-o', output, '-Hp', prop, dataset_name)
        try:
            out = self._perform_binary_command(cmd)
        except CalledProcessError as e:
            msg = 'Error while calling: %r, %s' % (cmd, e.output.strip())
            logger.warning(msg)
            size = '0'
        else:
            size = out.strip()

        return size

    def zfs_get_used_size(self, dataset_name):
        return int(self.zfs_get_property(dataset_name, 'used'))

    def zfs_get_referenced_size(self, dataset_name, snapname=None):
        return int(self.zfs_get_property(
            dataset_name, 'referenced', snapname=snapname))

    def zfs_create(self, dataset_name):
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
                self._perform_binary_command(('set', 'canmount=noauto', part))
            else:
                assert type_ == 'filesystem', (dataset_name, part, type_)

        # After mount, make it ours. Blegh. Unfortunate side-effect of
        # using sudo for the ZFS create.
        try:
            self.zfs_mount(dataset_name)
        except CalledProcessError:
            pass  # already mounted (we hope?)
        path = self.zfs_get_local_path(dataset_name)
        self._perform_sudo_command(('chown', str(os.getuid()), path))

        # Log something.
        logger.info('Created ZFS dataset: %s' % dataset_name)

    def zfs_mount(self, dataset_name):
        # Even if we have user-powers on /dev/zfs, we still cannot call
        # all commands.
        # $ /sbin/zfs mount tank/BACKUP/example-example
        # mount: only root can use "--options" option
        # cannot mount 'tank/BACKUP/example-example': Invalid argument
        # Might as well use sudo everywhere then.
        self._perform_binary_command(('mount', dataset_name))

    def zfs_unmount(self, dataset_name):
        self._perform_binary_command(('unmount', dataset_name))

    def zfs_rename_dataset(self, old_dataset_name, new_dataset_name):
        self._perform_binary_command(
            ('rename', old_dataset_name, new_dataset_name))

    # (old style)

    def snapshot_create(self, dataset_name, snapname):
        snapshot_name = '{}@{}'.format(dataset_name, snapname)
        cmd = ('snapshot', snapshot_name)
        self._perform_binary_command(cmd)
        return snapshot_name

    def snapshot_delete(self, dataset_name, snapname):
        cmd = ('destroy', '{}@{}'.format(dataset_name, snapname))
        self._perform_binary_command(cmd)

    def snapshot_list(self, dataset_name, typ=None):
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
                # Do not include the dataset in the snapshot name.
                snapshots.append(snapshot.split('@', 1)[1])
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

    def snapshots_rotate(self, dataset_name, **kwargs):
        snapshots = self.snapshot_list(dataset_name)
        destroyed = []
        logger.info('snapshots rotation for {}'.format(dataset_name))
        for snapname in snapshots:
            snaptype, dts = re.match(r'(\w+)-(\d+)', snapname).groups()
            snapshot_retain_func = getattr(self,
                                           'snapshot_retain_%s' % snaptype)
            retention = kwargs.get('%s_retention' % snaptype)
            if not snapshot_retain_func(snapname, retention):
                self.snapshot_delete(dataset_name, snapname)
                destroyed.append(snapname)
                logger.info(
                    'destroyed: %s@%s, past retention %s',
                    dataset_name, snapname, retention)
        return destroyed


class ZfsDataset(Dataset):
    # TODO/FIXME: check these methods and add them as NotImplemented to the
    # base

    def ensure_exists(self):
        if self.backend.binary == '/bin/true':
            return

        # Common case is the unmounted yet existing path. If the mount point
        # exists, everything in it should be fine too.
        if self.get_mount_path():
            return

        # Try creating it. (Creation also mounts it.)
        self.backend.zfs_create(self.name)

        # Now it should exist. Create the 'data' subdirectory as well.
        if hasattr(self, '_get_mount_path'):
            del self._get_mount_path
        if hasattr(self, '_get_data_path'):
            del self._get_data_path

        path = self.get_data_path()
        os.makedirs(path, 0o700)

        # Unmount if possible.
        try:
            self.backend.zfs_unmount(self.name)
        except CalledProcessError:
            pass

    @contextmanager
    def workon(self, data_path=None):
        cwd = os.getcwd()
        try:
            os.chdir('/')
            self.begin_work(data_path)
            yield
        finally:
            self.end_work()
            os.chdir(cwd)

    def begin_work(self, data_path=None):
        assert os.getcwd() == '/', os.getcwd()

        # The path we want to be in should be a subdirectory of the mount
        # point. Otherwise we cannot be sure that we have it locked.
        path = data_path or self.get_data_path()
        assert path.startswith(self.get_mount_path() + '/'), path

        # Try mounting a few times. There could be someone unmounting it just
        # now.
        for attempt in (1, 2, 3):
            try:
                # Attempt mount.
                self.backend.zfs_mount(self.name)  # zfs dataset
            except CalledProcessError:
                # Maybe it was already mounted?
                pass

            try:
                # Quickly jump into it. If it was already mounted, or we
                # mounted it just now, this should succeed.
                os.chdir(path)
            except FileNotFoundError:
                # Wait a bit before retrying.
                time.sleep(5)
            else:
                # Success!
                break
        else:
            # No luck after the Nth attempt. Fail.
            raise ValueError('Failed to work on {!r} ({})'.format(
                path, self.name))  # FIXME: better exception

    def end_work(self):
        # Leave directory, so it can be unmounted.
        os.chdir('/')
        try:
            self.backend.zfs_unmount(self.name)  # zfs dataset
        except CalledProcessError:
            # Ok. This might be because someone else is using it. Ignore.
            pass

        # Note that the mount point directory stays, but it will be
        # empty/unmounted (and owned by root) at this point.
        assert os.getcwd() == '/', os.getcwd()

    def get_mount_path(self):
        if not hasattr(self, '_get_mount_path'):
            ret = self.backend.zfs_get_local_path(self.name)
            if not ret:
                return None  # no negative cache

            self._get_mount_path = ret
        return self._get_mount_path

    def get_data_path(self):
        if not hasattr(self, '_get_data_path'):
            local_path = self.get_mount_path()
            if not local_path:
                raise ValueError(
                    'path {!r} for {!r} does not exist'.format(
                        local_path, self.name))
            self._get_data_path = os.path.join(local_path, 'data')
        return self._get_data_path

    def get_snapshot_path(self, snapshot):
        '''
        Return the path to the hidden snapshot directory.
        '''
        return os.path.abspath(os.path.join(
            self.get_data_path(), '../.zfs/snapshot', snapshot, 'data'))

    def get_used_size(self):
        return self.backend.zfs_get_used_size(self.name)

    def get_referenced_size(self, snapname=None):
        return self.backend.zfs_get_referenced_size(
            self.name, snapname)

    def rename_dataset(self, new_dataset_name):
        # Cannot rename while working from the dataset directory.
        # zfs rename will force a unmount/remount sequence for the filesystem
        # and any descendent file systems.
        assert not os.getcwd().startswith(self.get_mount_path()), (
            'Cannot rename dataset {} while working from dataset directory '
            '{}'.format(self.get_mount_path(), os.getcwd()))

        self.backend.zfs_rename_dataset(self.name, new_dataset_name)
        self.name = new_dataset_name

        # Clear cached properties.
        if hasattr(self, '_get_mount_path'):
            del self._get_mount_path
        if hasattr(self, '_get_data_path'):
            del self._get_data_path
