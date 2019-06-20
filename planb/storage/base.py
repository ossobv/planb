import logging

from planb.common.subprocess2 import CalledProcessError, check_output

logger = logging.getLogger(__name__)


class DatasetNotFound(Exception):
    pass


class Storage(object):
    def __init__(self, bfs, poolname):
        self._bfs = bfs
        self._poolname = poolname

    def get_dataset(self, customer, friendly_name):
        return self._bfs.get_dataset(self._poolname, customer, friendly_name)


class Datasets(list):
    """
    A list of Dataset objects.
    """
    @staticmethod
    def get_database_class():
        from planb.core.models import Fileset
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
        configs_by_identifier = {}
        for config in self.get_database_class().objects.all():
            identifier = config.get_dataset().identifier
            configs_by_identifier[identifier] = config

        for dataset in self:
            # Set all database_object's to the corresponding object or False if
            # not found.
            config = configs_by_identifier.get(dataset.identifier, False)
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
            (instance._database_object and
             instance._database_object.hostgroup.name or ''),
            instance._backend.name, instance.identifier)

    def __init__(self, backend, identifier):
        self.identifier = identifier
        self._backend = backend
        self._disk_usage = None
        self._database_object = None  # of type Fileset

    def __repr__(self):
        return '<{}:{}>'.format(self._backend.name, self.identifier)

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


class OldStyleStorage(object):
    name = NotImplemented

    def __init__(self, binary, sudobin):
        self.binary = binary
        self.sudobin = sudobin

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

    def snapshot_create(self, rootdir, customer, friendly_name):
        raise NotImplementedError()

    def snapshot_delete(self, rootdir, snapshot):
        raise NotImplementedError()

    def snapshot_retain_weekly(self, datetimestamp, retention):
        raise NotImplementedError()

    def snapshot_retain_monthly(self, datetimestamp, retention):
        raise NotImplementedError()

    def snapshot_retain_yearly(self, datetimestamp, retention):
        raise NotImplementedError()

    def snapshots_get(self, rootdir, customer, friendly_name):
        raise NotImplementedError()

    def snapshots_rotate(self, rootdir, customer, friendly_name, retention):
        raise NotImplementedError()
