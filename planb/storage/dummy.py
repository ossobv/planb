import os.path
from tempfile import TemporaryDirectory

from .base import Dataset, Storage


class DummyStorage(Storage):
    '''
    DummyStorage is a simple in-memory storage that will store the names and
    snapshots of any datasets you request from it.
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._datasets = {}

    def close(self):
        for dataset in self.get_datasets():
            dataset.close()

    def get_datasets(self):
        return list(self._datasets.values())

    def get_dataset(self, dataset_name):
        if dataset_name not in self._datasets:
            self._datasets[dataset_name] = DummyDataset(
                storage=self, name=dataset_name)
        return self._datasets[dataset_name]

    def snapshot_create(self, dataset_name, snapname):
        dataset = self.get_dataset(dataset_name)
        return dataset.snapshot_create(snapname)

    def snapshot_delete(self, dataset_name, snapname):
        dataset = self.get_dataset(dataset_name)
        return dataset.snapshot_delete(snapname)

    def snapshot_list(self, dataset_name):
        dataset = self.get_dataset(dataset_name)
        return dataset.snapshot_list()


class DummyDataset(Dataset):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._snapshots = []
        self._temp_directory = None

    def close(self):
        if self._temp_directory is not None:
            self._temp_directory.cleanup()
            self._temp_directory = None

    def get_data_path(self):
        return os.path.join(self.temp_directory, 'data')

    def get_referenced_size(self):
        return 1001

    def get_snapshot_path(self, snapname):
        snapshot = os.path.join(
            self.temp_directory, '.snapshot', snapname, 'data')
        if not os.path.exists(snapshot):
            os.makedirs(snapshot)
        return snapshot

    def get_used_size(self):
        return 1001

    def rename_dataset(self, new_dataset_name):
        self._storage._datasets.pop(self.name)
        self.name = new_dataset_name
        self._storage._datasets[self.name] = self

    def snapshot_create(self, snapname):
        if snapname in self._snapshots:
            raise ValueError('Snapshot with name {} exists'.format(snapname))
        self._snapshots.append(snapname)
        return snapname

    def snapshot_delete(self, snapname):
        self._snapshots.remove(snapname)
        return snapname

    def snapshot_list(self):
        # Sort snapshots by creation date.
        return sorted(self._snapshots, key=lambda i: i.split('-')[-1])

    @property
    def temp_directory(self):
        '''
        Create a safe temporary directory to play with.
        The directory will be destroyed when the Dataset object with the
        _temp_directory property is destroyed.
        '''
        if self._temp_directory is None:
            self._temp_directory = TemporaryDirectory()
        return self._temp_directory.name
