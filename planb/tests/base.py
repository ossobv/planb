from django.test import TestCase

from planb.storage import load_storage_pools, storage_pools


class PlanbTestCase(TestCase):
    def setUp(self):
        super().setUp()

        # Reset storage_pools, otherwise we might get stale data from
        # previous dummy's. This is a dict, that everyone has loaded
        # already. Flush the contents of the dict.
        for storage in storage_pools.values():
            storage.close()
        storage_pools.clear()
        storage_pools.update(load_storage_pools())
