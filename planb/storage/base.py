from contextlib import contextmanager
import datetime
import logging
import re

from dateutil.relativedelta import relativedelta, SU

logger = logging.getLogger(__name__)

# regex to get the datetime from a snapshot name.
# the optional prefix can be ignored.
SNAPNAME_DATETIME_RE = re.compile(r'^(?:planb-)?(\d{8}T\d{4}Z)$')


class RetentionPeriod:
    def __init__(self, clamp, delta):
        # Clamp the desired datetime to this value.
        self.clamp = clamp
        # Subtract this delta to determine the next desired datetime.
        # Ensure the delta also clamps the datetime.
        self.delta = clamp + delta

    def __call__(self, snapshot_dts, previous_dts):
        # The snapshot_dts may be newer than the previous clamped dts
        # resulting in the same clamped value. If the value was older it yields
        # the correct next dts without subtracting the delta.
        dts = snapshot_dts - self.clamp
        # If the desired_dts is newer than the current snapshot or if we have
        # the same datetime as the previous clamped value we have to subtract
        # the delta value and clamp it again.
        if dts >= snapshot_dts or (
                previous_dts and dts == previous_dts - self.clamp):
            dts -= self.delta
        return dts


RETENTION_PERIOD_SECONDS = {
    'h': RetentionPeriod(
        clamp=relativedelta(minute=0, second=0, microsecond=0),
        delta=relativedelta(hours=1)),
    'd': RetentionPeriod(
        clamp=relativedelta(hour=0, minute=0, second=0, microsecond=0),
        delta=relativedelta(days=1)),
    'w': RetentionPeriod(
        clamp=relativedelta(
            hour=0, minute=0, second=0, microsecond=0, weekday=SU(1)),
        delta=relativedelta(days=7)),
    'm': RetentionPeriod(
        clamp=relativedelta(day=1, hour=0, minute=0, second=0, microsecond=0),
        delta=relativedelta(months=1)),
    'y': RetentionPeriod(
        clamp=relativedelta(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
        delta=relativedelta(years=1)),
}


def datetime_from_snapshot_name(snapname):
    try:
        dts = SNAPNAME_DATETIME_RE.match(snapname).group(1)
        dts = parse_snapshot_datetime(dts)
    except (AttributeError, TypeError, ValueError):
        raise ValueError('no match for {!r}'.format(snapname))
    return dts


def parse_snapshot_datetime(value):
    return datetime.datetime.strptime(value, '%Y%m%dT%H%MZ')  # planb-dTtZ


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

    def close(self):
        pass

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
                dts = datetime_from_snapshot_name(snapname)
            except ValueError:
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

        keep_snapshots = SnapshotRetentionManager(
            dataset_name, snapshots, retention_map).get_snapshots_to_keep()
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


class SnapshotRetentionManager:
    def __init__(self, dataset_name, snapshots, retention_map):
        self.dataset_name = dataset_name
        self.snapshots = list(sorted(snapshots, reverse=True))
        self.retention_map = retention_map
        self.newest_dts, self.newest_snapshot = self.snapshots[0]
        # Always keep the newest snapshot.
        self.keep_snapshots = {self.newest_snapshot}

    def get_snapshots_to_keep(self):
        # Go through the retention configuration and decide which snapshots
        # should be kept.
        for period, retention in self.retention_map.items():
            self.find_snapshots_for_retention_period(
                period, retention, self.newest_dts)
            if len(self.keep_snapshots) == len(self.snapshots):
                break
        # Return with the same ordering.
        return [i[1] for i in self.snapshots if i[1] in self.keep_snapshots]

    def find_snapshots_for_retention_period(
            self, period, retention, snapshot_dts):
        retention_period = RETENTION_PERIOD_SECONDS[period]
        desired_dts = None
        for i in range(retention):
            # Find the next best snapshot from the previous match to
            # increase coverage on systems with irregular schedules.
            desired_dts = retention_period(snapshot_dts, desired_dts)
            previous_dts = snapshot_dts
            snapshot_dts = self.find_snapshot_for_desired_dts(
                period, desired_dts, previous_dts)
            if (snapshot_dts is None or snapshot_dts == previous_dts
                    or len(self.keep_snapshots) == len(self.snapshots)):
                break

    def find_snapshot_for_desired_dts(self, period, desired_dts, previous_dts):
        # For each desired snapshot we need to keep the best matching snapshot
        # and the snapshot that will become the best match.
        best_difference = best_dts = best_snapshot = None
        for i, (dts, snapname) in enumerate(self.snapshots, -1):
            if dts >= previous_dts:  # Skip snapshots newer than the previous.
                continue
            difference = (desired_dts - dts).total_seconds()
            if (best_difference is None
                    or abs(difference) < abs(best_difference)):
                best_difference = difference
                best_snapshot = snapname
                best_dts = dts
            elif abs(difference) > abs(best_difference):
                break
        if best_snapshot is not None:
            logger.debug(
                '[%s] Select %s as best match for %s:%s with diff:%d',
                self.dataset_name, best_snapshot, period,
                desired_dts.strftime('%Y%m%dT%H%MZ'), best_difference)
            self.keep_snapshots.add(best_snapshot)
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

    def keep_only_leaves(self):
        """
        We generally only want the leaves, except when our dataset handles one
        or more ZFS datasets on remote.

          [DEL] tank/namespace
                tank/namespace/dataset1
                tank/namespace/dataset2
                tank/namespace/dataset3-zfs
          [DEL] tank/namespace/dataset3-zfs/etc
          [DEL] tank/namespace/dataset3-zfs/var-backups

        For this to work properly, the dataset list must be fully populated and
        load_database_config must have been called.

        (If 'tank/namespace/dataset3-zfs' was not in the list, we would *not*
        remove its two leaves.)
        """
        # Filter, get only leaves OR those which exist in the database.
        datasets_in_database = [ds for ds in self if ds.exists_in_database]
        datasets_by_name = set(ds.name for ds in datasets_in_database)
        relevant_datasets = datasets_in_database
        for ds in self:
            # We already have those that exist in the database.
            if ds.exists_in_database:
                pass  # already in it
            # Now we add only leaves, except for leaves that are part of an
            # existing (in the database) dataset.
            elif ds.is_leaf:
                # foo/bar/baz => ['foo', 'foo/bar']
                components = ds.name.split('/')
                parents = [
                    '/'.join(components[0:i])
                    for i in range(1, len(components))]
                # ('foo' not in datasets_by_name) and
                # ('foo/bar' not in datasets_by_name)
                if all(i not in datasets_by_name for i in parents):
                    relevant_datasets.append(ds)

        # Overwrite self.
        self[:] = relevant_datasets


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
        self._is_leaf = None

    def __repr__(self):
        return '<{}:{}>'.format(self._storage.name, self.name)

    def close(self):
        pass

    def flush(self):
        self._disk_usage = None

    @property
    def exists_in_database(self):
        assert self._database_object is not None
        return (self._database_object is not False)

    @property
    def is_leaf(self):
        assert self._is_leaf is not None
        return self._is_leaf

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

    def set_leaf(self, is_leaf):
        assert self._is_leaf is None
        assert isinstance(is_leaf, bool), is_leaf
        self._is_leaf = is_leaf

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
