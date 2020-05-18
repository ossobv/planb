from contextlib import contextmanager
import datetime
import logging
import re

logger = logging.getLogger(__name__)

# regex to get the datetime from a snapshot name.
# the optional prefix can be ignored.
SNAPNAME_DATETIME_RE = re.compile(r'^(?:\w+-)?(\d{8}T?\d{4}Z?)$')

RETENTION_PERIOD_SECONDS = {
    'h': 3600,
    'd': 24 * 3600,
    'w': 7 * 24 * 3600,
    'm': 30 * 24 * 3600,
    'y': 365 * 24 * 3600,

}


def parse_snapshot_datetime(value):
    for pattern in (
            '%Y%m%dT%H%MZ',  # planb-newTtimeZ
            '%Y%m%dT%H%M',   # planb-veryTemporary
            '%Y%m%d%H%M'):   # daily-oldtimestamp
        try:
            return datetime.datetime.strptime(value, pattern)
        except (TypeError, ValueError):
            pass
    raise ValueError('Invalid timestamp')


class DatasetNotFound(Exception):
    pass


class Storage(object):
    'Private/friend parts for storage backends.'
    @classmethod
    def ensure_defaults(cls, config):
        config.setdefault('NAME', cls.__name__)

    def __init__(self, config, alias):
        self.config = config
        self.name = config['NAME']
        self.alias = alias

    def get_label(self):
        return self.name

    def get_datasets(self):
        '''
        Return a list of Dataset objects found in the storage.

        Example implementation::

            return Datasets([
                Dataset(zfs_storage, directory)
                for directory in `zfs list -Hpo name`])
        '''
        raise NotImplementedError()

    def get_dataset(self, dataset_name):
        raise NotImplementedError()

    def name_dataset(self, namespace, name):
        return '{}-{}'.format(namespace, name)

    def snapshot_create(self, dataset_name, snapname):
        raise NotImplementedError()

    def snapshot_delete(self, dataset_name, snapname):
        raise NotImplementedError()

    def snapshot_list(self, dataset_name):
        raise NotImplementedError()

    def snapshot_rotate(self, dataset_name, retention_map):
        '''
        Rotate the snapshots according to the retention parameters.
        '''
        snapshots = []
        logger.info(
            '[%s] Snapshots rotation using retention: %s',
            dataset_name, retention_map)
        for snapname in self.snapshot_list(dataset_name):
            try:
                dts = SNAPNAME_DATETIME_RE.match(snapname).group(1)
                dts = parse_snapshot_datetime(dts)
            except (AttributeError, TypeError, ValueError):
                logger.info(
                    '[%s] Keeping manual snapshot %s', dataset_name, snapname)
                continue
            snapshots.append((dts, snapname))

        snapshots = list(sorted(snapshots, reverse=True))
        logger.info(
            '[%s] Available snapshots: %s', dataset_name,
            [i[1] for i in snapshots])
        if not snapshots:
            return []

        keep_snapshots = self._get_snapshots_to_keep(
            dataset_name, snapshots, retention_map)
        logger.info('[%s] Keeping snapshots: %s', dataset_name, keep_snapshots)

        # Delete the snapshots which are not kept.
        destroyed = []
        for dts, snapname in snapshots:
            if snapname in keep_snapshots:
                continue
            destroyed.append(snapname)
            self.snapshot_delete(dataset_name, snapname)
            logger.info('[%s] Destroyed snapshot: %s', dataset_name, snapname)
        return destroyed

    def _get_snapshots_to_keep(self, dataset_name, snapshots, retention_map):
        # Go through all snapshots and decide which should be kept.
        snapshots = list(sorted(snapshots, reverse=True))
        latest_dts, latest_snapshot = snapshots[0]
        # Always keep the latest snapshot.
        keep_snapshots = {latest_snapshot}
        for period, retention in retention_map.items():
            next_dts = latest_dts
            period_in_seconds = RETENTION_PERIOD_SECONDS[period]
            for i in range(retention):
                # Find the next best snapshot from the previous match to
                # increase coverage on systems with irregular schedules.
                next_dts = self._find_snapshot_for_desired_dts(
                    dataset_name, snapshots, keep_snapshots,
                    next_dts - datetime.timedelta(
                        seconds=period_in_seconds))
                if len(keep_snapshots) == len(snapshots):
                    break
            else:
                continue
            # Inner loop was broken.
            break
        # Return with the same ordering.
        return [i[1] for i in snapshots if i[1] in keep_snapshots]

    def _find_snapshot_for_desired_dts(
            self, dataset_name, snapshots, keep_snapshots, desired_dts):
        # For each desired snapshot we need to keep the best matching snapshot
        # and the snapshot that will become the best match.
        best_difference = best_dts = best_snapshot = None
        for i, (dts, snapname) in enumerate(snapshots[1:], -1):
            difference = (desired_dts - dts).total_seconds()
            if (best_difference is None
                    or abs(difference) < abs(best_difference)):
                best_difference = difference
                best_snapshot = snapname
                best_dts = dts
            elif abs(difference) > abs(best_difference):
                if best_difference > 0:
                    # The snapshot is going stale, include a new snapshot.
                    fresh_snapname = snapshots[i][1]
                    logger.debug(
                        '[%s] Select %s as fresh match for %s',
                        dataset_name, fresh_snapname,
                        desired_dts.strftime('%Y%m%dT%H%MZ'))
                    keep_snapshots.add(fresh_snapname)
                break
        if best_snapshot is not None:
            logger.debug(
                '[%s] Select %s as best match for %s with diff:%d',
                dataset_name, best_snapshot,
                desired_dts.strftime('%Y%m%dT%H%MZ'), best_difference)
            keep_snapshots.add(best_snapshot)
        return best_dts


class Datasets(list):
    """
    A list of Dataset objects.
    """
    @staticmethod
    def get_database_class():
        from planb.models import Fileset
        return Fileset

    def sort(self, key=None, reverse=False):
        """
        Sort datasets (by name).
        """
        if not key:
            key = Dataset.sortkey_by_name

        return super().sort(key=key, reverse=reverse)

    def load_database_config(self):
        """
        Set reference to database instances (of type Fileset).
        """
        configs_by_dataset = {}
        for config in self.get_database_class().objects.all():
            configs_by_dataset[config.dataset_name] = config

        for dataset in self:
            # Set all database_object's to the corresponding object or False if
            # not found.
            config = configs_by_dataset.get(dataset.name, False)
            dataset.set_database_object(config)  # of type Fileset


class Dataset(object):
    """
    New-style entry into data sets.

    Storage(zfs pools)->Dataset(directory)->Snapshot(directory snapshot)
    """
    @staticmethod
    def sortkey_by_name(dataset):
        """
        Common sort key to sort the dataset list.

        Instead of doing datasets.sort(), we do
        datasets.sort(key=Dataset.sortkey_by_name) which will fetch the
        builtin types O(n) times and then sort in C.

        If we used the __eq__ and __lt__ operators, they would be called
        O(n^2) times.
        """
        return (
            (dataset._database_object
                and dataset._database_object.hostgroup.name or ''),
            dataset._storage.name, dataset.name)

    def __init__(self, storage, name):
        self._storage = storage
        self.name = name
        self._disk_usage = None
        self._database_object = None  # of type Fileset

    def __repr__(self):
        return '<{}:{}>'.format(self._storage.name, self.name)

    def flush(self):
        self._disk_usage = None

    @property
    def exists_in_database(self):
        assert self._database_object is not None
        return (self._database_object is not False)

    @property
    def database_object(self):
        return self._database_object  # might be None

    @property
    def disk_usage(self):
        assert self._disk_usage is not None
        return self._disk_usage

    def set_disk_usage(self, usage):
        assert self._disk_usage is None
        self._disk_usage = usage

    def set_database_object(self, database_object):
        assert self._database_object is None
        self._database_object = database_object

    def get_referenced_size(self, snapname=None):
        raise NotImplementedError()

    def get_used_size(self):
        raise NotImplementedError()

    def ensure_exists(self):
        pass

    def has_child_datasets(self):
        return False

    def get_child_datasets(self):
        raise NotImplementedError()

    def get_data_path(self):
        raise NotImplementedError()

    def get_snapshot_path(self, snapname):
        raise NotImplementedError()

    def rename_dataset(self, new_dataset_name):
        raise NotImplementedError()

    def snapshot_create(self, snapname):
        return self._storage.snapshot_create(self.name, snapname)

    def snapshot_delete(self, snapname):
        return self._storage.snapshot_delete(self.name, snapname)

    def snapshot_list(self):
        return self._storage.snapshot_list(self.name)

    def snapshot_rotate(self, retention_map):
        return self._storage.snapshot_rotate(self.name, retention_map)

    def child_dataset_snapshot_rotate(self, retention_map):
        '''
        Rotate the snapshots for all child datasets and return all unique
        destroyed snapshots.
        '''
        if not self.has_child_datasets():
            raise ValueError('Dataset has no child datasets')
        # Call the storage directly, child datasets are not guaranteed to be
        # recursion safe.
        destroyed = set()
        for dataset in self.get_child_datasets():
            destroyed.update(
                self._storage.snapshot_rotate(
                    dataset.name, retention_map))
        return list(destroyed)

    def child_dataset_snapshot_list(self):
        '''
        Returns snapshots which are available for *all* child datasets.
        '''
        if not self.has_child_datasets():
            raise ValueError('Dataset has no child datasets')

        snapshots = None
        for dataset in self.get_child_datasets():
            if snapshots is None:
                snapshots = set(self._storage.snapshot_list(dataset.name))
            else:
                snapshots.intersection_update(
                    self._storage.snapshot_list(dataset.name))
        return snapshots

    @contextmanager
    def workon(self, data_path=None):
        yield
