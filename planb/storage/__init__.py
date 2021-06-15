from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from django.utils.functional import SimpleLazyObject


class StorageWrapper:
    '''
    Public exposed API for the Storage class.
    '''
    def __init__(self, storage):
        self._storage = storage

    def __str__(self):
        return 'StorageWrapper({})'.format(self._storage)

    def __repr__(self):
        return 'StorageWrapper({!r})'.format(self._storage)

    @property
    def alias(self):
        return self._storage.alias

    def close(self):
        return self._storage.close()

    def get_label(self):
        return self._storage.get_label()

    def get_datasets(self):
        sets = self._storage.get_datasets()
        # XXX: do i.get_parent_dataset() instead?
        ds_parents = set([i.name.rsplit('/', 1)[0] for i in sets])
        for ds in sets:
            is_parent = bool(ds.name in ds_parents)
            ds.set_leaf(is_leaf=(not is_parent))
        return sets

    def name_dataset(self, namespace, name):
        return self._storage.name_dataset(namespace, name)

    def get_dataset(self, dataset_name):
        return self._storage.get_dataset(dataset_name)


def load_storage_pools():
    if not isinstance(settings.PLANB_STORAGE_POOLS, dict):
        raise ImproperlyConfigured(
            'The PLANB_STORAGE_POOLS settings has been modified, check the '
            'example settings reference.')
    pools = {}
    for alias, config in settings.PLANB_STORAGE_POOLS.items():
        config.setdefault('ENGINE', 'planb.storage.dummy.DummyStorage')
        StorageImpl = import_string(config['ENGINE'])
        StorageImpl.ensure_defaults(config)
        pools[alias] = StorageWrapper(StorageImpl(config, alias))
    return pools


storage_pools = SimpleLazyObject(load_storage_pools)
