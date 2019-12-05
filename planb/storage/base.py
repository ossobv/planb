from contextlib import contextmanager
import logging

from planb.common.subprocess2 import CalledProcessError, check_output

logger = logging.getLogger(__name__)


class DatasetNotFound(Exception):
    pass


class Storage(object):
    def __init__(self, config, alias):
        self.config = config
        self.name = config['NAME']
        self.alias = alias

    def get_label(self):
        return self.name

    @classmethod
    def ensure_defaults(cls, config):
        config.setdefault('NAME', cls.__name__)

    def get_dataset_name(self, namespace, name):
        return '{}-{}'.format(namespace, name)

    def get_datasets(self):
        raise NotImplementedError()

    def get_dataset(self, dataset_name):
        raise NotImplementedError()

    def snapshot_create(self, dataset_name, snapname):
        raise NotImplementedError()

    def snapshot_list(self, dataset_name):
        raise NotImplementedError()

    def snapshots_rotate(self, dataset_name, **kwargs):
        '''
        Rotate the snapshots according to the retention parameters in kwargs.
        '''
        raise NotImplementedError()


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
    def sortkey_by_name(instance):
        """
        Common sort key to sort the dataset list.

        Instead of doing datasets.sort(), we do
        datasets.sort(key=Dataset.sortkey_by_name) which will fetch the
        builtin types O(n) times and then sort in C.

        If we used the __eq__ and __lt__ operators, they would be called
        O(n^2) times.
        """
        return (
            (instance._database_object
                and instance._database_object.hostgroup.name or ''),
            instance.backend.name, instance.name)

    def __init__(self, backend, name):
        self.backend = backend
        self.name = name
        self._disk_usage = None
        self._database_object = None  # of type Fileset

    def __repr__(self):
        return '<{}:{}>'.format(self.backend.name, self.name)

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

    def get_referenced_size(self):
        raise NotImplementedError()

    def get_used_size(self):
        raise NotImplementedError()

    def ensure_exists(self):
        pass

    def get_data_path(self):
        raise NotImplementedError()

    def get_snapshot_path(self, snapname):
        raise NotImplementedError()

    def rename_dataset(self, new_dataset_name):
        raise NotImplementedError()

    @contextmanager
    def workon(self, data_path=None):
        yield


class OldStyleStorage(Storage):
    name = NotImplemented

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.binary = self.config['BINARY']
        self.sudobin = self.config['SUDOBIN']

    @classmethod
    def ensure_defaults(cls, config):
        super().ensure_defaults(config)
        config.setdefault('BINARY', '/sbin/zfs')
        config.setdefault('SUDOBIN', '/usr/bin/sudo')

    def __perform_system_command(self, cmd):
        """
        Do exec command, expect 0 return value, convert output to utf-8.
        """
        try:
            output = check_output(cmd)
        except CalledProcessError as e:
            logger.info('Non-zero exit after cmd {!r}: {}'.format(
                cmd, e))
            raise
        return output.decode('utf-8')  # expect valid ascii/utf-8

    def _perform_sudo_command(self, cmd):
        """
        Do __perform_system_command, but with 'sudo'.
        """
        return self.__perform_system_command(
            (self.sudobin,) + tuple(cmd))

    def _perform_binary_command(self, cmd):
        """
        Do _perform_sudo_command, but for the supplied binary.
        """
        return self._perform_sudo_command(
            (self.binary,) + tuple(cmd))

    def get_datasets(self):
        """
        Return a list of Dataset objects found in the storage.

        Example implementation::

            return Datasets([
                Dataset(zfs_backend, directory)
                for directory in `zfs list -Hpo name`])
        """
        raise NotImplementedError()

    def snapshot_retain_weekly(self, datetimestamp, retention):
        raise NotImplementedError()

    def snapshot_retain_monthly(self, datetimestamp, retention):
        raise NotImplementedError()

    def snapshot_retain_yearly(self, datetimestamp, retention):
        raise NotImplementedError()
