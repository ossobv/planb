from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from django.utils.functional import SimpleLazyObject


def load_pools():
    if not isinstance(settings.PLANB_STORAGE_POOLS, dict):
        raise ImproperlyConfigured(
            'The PLANB_STORAGE_POOLS settings has been modified, check the '
            'example settings reference.')
    pools = {}
    for alias, config in settings.PLANB_STORAGE_POOLS.items():
        config.setdefault('ENGINE', 'planb.storage.dummy.DummyStorage')
        Storage = import_string(config['ENGINE'])
        Storage.ensure_defaults(config)
        pools[alias] = Storage(config, alias)
    return pools


pools = SimpleLazyObject(load_pools)
