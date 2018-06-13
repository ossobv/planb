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
            name, used = line.split('\t')

            if any(name.startswith(i[1] + '/')
                   for i in settings.PLANB_STORAGE_POOLS):
                dataset = Dataset(backend=self, identifier=name)
                dataset.set_disk_usage(int(used))
                datasets.append(dataset)

        return datasets

    def data_dir_create(self, rootdir, customer, friendly_name):
        cmd = (
            'create',
            self.get_dataset_name(rootdir, customer, friendly_name))
        self._perform_binary_command(cmd)
        # After mount, make it ours. Blegh. Unfortunate side-effect of
        # using sudo for the ZFS create.
        self._perform_sudo_command(
            ('chown', str(os.getuid()), self._root_dir_get(
                rootdir, customer, friendly_name)))

        logger.info('Created ZFS dataset: %s' % self.get_dataset_name(
            rootdir, customer, friendly_name))

    def get_dataset_name(self, rootdir, customer, friendly_name):
        return '{0}/{1}-{2}'.format(rootdir, customer, friendly_name)

    def _root_dir_get(self, rootdir, customer, friendly_name):
        dataset_name = self.get_dataset_name(rootdir, customer, friendly_name)
        cmd = (
            'get', '-Ho', 'value', 'mountpoint', dataset_name)
        try:
            out = self._perform_binary_command(cmd).rstrip('\r\n')
        except CalledProcessError:
            out = None
        return out

    def data_dir_get(self, rootdir, customer, friendly_name):
        root_dir = self._root_dir_get(rootdir, customer, friendly_name)
        if not root_dir:
            return root_dir  # =None
        return os.path.join(root_dir, 'data')

    def snapshot_create(self, rootdir, customer, friendly_name, snapname=None):
        datasetname = self.get_dataset_name(rootdir, customer, friendly_name)
        snapshot_name = '{0}@{1}'.format(datasetname, snapname)
        cmd = ('snapshot', snapshot_name)
        self._perform_binary_command(cmd)
        return snapshot_name

    def snapshot_delete(self, snapshot):
        cmd = ('destroy', snapshot)
        self._perform_binary_command(cmd)

    def snapshots_get(self, rootdir, customer, friendly_name, typ=None):
        cmd = (
            'list', '-r', '-H', '-t', 'snapshot', '-o', 'name',
            self.get_dataset_name(rootdir, customer, friendly_name))

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
            snapshot_rgx = re.compile('^.*@\w+-\d+$')
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
        snapshots = self.snapshots_get(rootdir, customer, friendly_name)
        destroyed = []
        logger.info('snapshots rotation for {0}'.format(
            self.get_dataset_name(rootdir, customer, friendly_name)))
        for snapshot in snapshots:
            ds, snapname = snapshot.split('@')
            snaptype, dts = re.match(r'(\w+)-(\d+)', snapname).groups()
            snapshot_retain_func = getattr(self,
                                           'snapshot_retain_%s' % snaptype)
            retention = kwargs.get('%s_retention' % snaptype)
            if not snapshot_retain_func(snapname, retention):
                self.snapshot_delete(snapshot)
                destroyed.append(snapshot)
                logger.info('destroyed: {0}, past retention'.format(
                        snapshot, retention))
        return destroyed

    def parse_backup_sizes(self, rootdir, customer, friendly_name):
        cmd = (
            'get', '-o', 'value', '-Hp', 'used',
            self.get_dataset_name(rootdir, customer, friendly_name))
        try:
            out = self._perform_binary_command(cmd)
        except CalledProcessError as e:
            msg = 'Error while calling: %r, %s' % (cmd, e.output.strip())
            logger.warning(msg)
            size = '0'
        else:
            size = out.strip()

        return int(size)
