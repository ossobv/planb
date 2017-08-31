from planb.common.subprocess2 import check_output


class Storage(object):
    def __init__(self, bfs, poolname):
        self._bfs = bfs
        self._poolname = poolname

    def get_identifier(self, identifier):
        # FIXME: should be ZFS-specific?
        return '/'.join([self._poolname, identifier])


class Datasets(list):
    """
    A list of Dataset objects.
    """
    @staticmethod
    def get_hostconfig_class():
        from planb.models import HostConfig
        return HostConfig

    def sort(self, key=None, reverse=False):
        """
        Sort datasets (by name).
        """
        if not key:
            key = Dataset.sortkey_by_name

        return super().sort(key=key, reverse=reverse)

    def load_hostconfigs(self):
        """
        Set reference to hostconfigs.
        """
        configs_by_identifier = {}
        for config in self.get_hostconfig_class().objects.all():
            identifier = config.get_storage().get_identifier(config.identifier)
            configs_by_identifier[identifier] = config

        for dataset in self:
            config = configs_by_identifier.get(dataset.identifier, False)
            dataset.set_hostconfig(config)


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
        return (instance._backend.name, instance.identifier)

    def __init__(self, backend, identifier):
        self.identifier = identifier
        self._backend = backend
        self._disk_usage = None
        self._hostconfig = None

    def __repr__(self):
        return '<{}:{}>'.format(self._backend.name, self.identifier)

    @property
    def disk_usage(self):
        assert self._hostconfig is not None
        return self._disk_usage

    @property
    def hostconfig(self):
        assert self._hostconfig is not None
        return self._hostconfig

    def set_disk_usage(self, usage):
        assert self._disk_usage is None
        self._disk_usage = usage

    def set_hostconfig(self, hostconfig):
        assert self._hostconfig is None
        self._hostconfig = hostconfig


class OldStyleStorage(object):
    name = NotImplemented

    def __init__(self, binary, sudobin):
        self.binary = binary
        self.sudobin = sudobin

    def _perform_system_command(self, cmd):
        """
        Do exec command, expect 0 return value, convert output to utf-8.
        """
        output = check_output(cmd)
        return output.decode('utf-8')  # expect valid ascii/utf-8

    def _perform_sudo_command(self, cmd):
        """
        Do _perform_system_command, but with 'sudo'.
        """
        return self._perform_system_command(
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

    def get_dataset_name(self, rootdir, customer, friendly_name):
        '''
        Should return a relative path leading to the backupdir, e.g.:
            testbackup/backupnameA
        '''
        raise NotImplementedError()

    def data_dir_create(self, rootdir, customer, friendly_name):
        '''
        Should create the datadir returned by data_dir_get()
        '''
        raise NotImplementedError()

    def data_dir_get(self, rootdir, customer, friendly_name):
        '''
        Should return an absolute path where to backup to e.g.:
            /backups/testbackup-backupnameA/data
        '''
        raise NotImplementedError()

    def can_backup(self, rootdir, customer, friendly_name):
        '''
        Helper function that should be used in the daily backup routine
        to check if a backup can be made.
        '''
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

    def parse_backup_sizes(self, rootdir, customer, friendly_name,
                           date_complete):
        '''
        Should return a dict of dicts containing size in bytes and dates.
        {
            'used': '123456789',
            'date': date_complete,
        }
        '''
        raise NotImplementedError()
