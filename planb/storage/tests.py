import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from planb.common.subprocess2 import CalledProcessError
from planb.storage import load_storage_pools
from planb.storage.dummy import DummyStorage
from planb.storage.zfs import ZfsStorage
from planb.tests.base import PlanbTestCase


class PlanbStorageTestCase(PlanbTestCase):
    maxDiff = None

    def test_config_loading(self):
        # PLANB_STORAGE_POOLS used to be a list.
        # To be fair, the user already fixed this if he can run the tests.
        with override_settings(PLANB_STORAGE_POOLS=[]), \
                self.assertRaises(ImproperlyConfigured):
            load_storage_pools()

    def get_dummy_storage(self):
        config = {'NAME': 'Dummy Storage'}
        DummyStorage.ensure_defaults(config)
        return DummyStorage(config, alias='dummy')

    def test_dummy_storage(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.rename_dataset('new_name')
        self.assertEqual(dataset.name, 'new_name')

        datasets = storage.get_datasets()
        self.assertEqual(len(datasets), 1)
        self.assertEqual(datasets[0].name, 'new_name')

    def test_snapshot_rotate(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('planb-20200502T1743Z')
        dataset.snapshot_create('planb-20200503T1801Z')
        dataset.snapshot_create('planb-20200504T1602Z')
        dataset.snapshot_create('hello')
        dataset.snapshot_create('planb-20200102T0912Z')
        dataset.snapshot_create('planb-20200504T1458Z')
        dataset.snapshot_create('planb-20200504T1655Z')
        dataset.snapshot_create('archive-20200504T1458Z')
        dataset.snapshot_create('planb-20200504T1700Z')
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200102T0912Z',
            'planb-20200502T1743Z',
            'planb-20200503T1801Z',
            'planb-20200504T1458Z',
            'archive-20200504T1458Z',
            'planb-20200504T1602Z',
            'planb-20200504T1655Z',
            'planb-20200504T1700Z',
            'hello',
        ])
        destroyed = dataset.snapshot_rotate(
            retention_map={'h': 2, 'y': 1})
        self.assertEqual(
            destroyed, ['planb-20200504T1655Z', 'planb-20200503T1801Z',
                        'planb-20200502T1743Z'])
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200102T0912Z',
            'planb-20200504T1458Z',
            'archive-20200504T1458Z',
            'planb-20200504T1602Z',
            'planb-20200504T1700Z',
            'hello',
        ])

    def test_snapshot_rotate_only_planb_prefix(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('archive-20200101T0000Z')
        dataset.snapshot_create('archive-20200201T0000Z')
        dataset.snapshot_create('archive-20200301T0000Z')
        dataset.snapshot_create('archive-20200401T0000Z')
        dataset.snapshot_create('archive-20200501T0000Z')
        dataset.snapshot_create('planb-20200601T0000Z')
        dataset.snapshot_create('archive-20200701T0000Z')
        dataset.snapshot_create('archive-20200801T0000Z')
        dataset.snapshot_create('archive-20200901T0000Z')
        dataset.snapshot_create('archive-20201001T0000Z')
        dataset.snapshot_create('archive-20201101T0000Z')
        dataset.snapshot_create('archive-20201201T0000Z')
        dataset.snapshot_create('planb-20210101T0000Z')
        dataset.snapshot_create('archive-20210201T0000Z')
        self.assertEqual(dataset.snapshot_list(), [
            'archive-20200101T0000Z',
            'archive-20200201T0000Z',
            'archive-20200301T0000Z',
            'archive-20200401T0000Z',
            'archive-20200501T0000Z',
            'planb-20200601T0000Z',
            'archive-20200701T0000Z',
            'archive-20200801T0000Z',
            'archive-20200901T0000Z',
            'archive-20201001T0000Z',
            'archive-20201101T0000Z',
            'archive-20201201T0000Z',
            'planb-20210101T0000Z',
            'archive-20210201T0000Z',
        ])
        destroyed = dataset.snapshot_rotate(retention_map={})  # "all"
        self.assertEqual(destroyed, ['planb-20200601T0000Z'])
        self.assertEqual(dataset.snapshot_list(), [
            'archive-20200101T0000Z',
            'archive-20200201T0000Z',
            'archive-20200301T0000Z',
            'archive-20200401T0000Z',
            'archive-20200501T0000Z',
            'archive-20200701T0000Z',
            'archive-20200801T0000Z',
            'archive-20200901T0000Z',
            'archive-20201001T0000Z',
            'archive-20201101T0000Z',
            'archive-20201201T0000Z',
            'planb-20210101T0000Z',    # keep one though
            'archive-20210201T0000Z',
        ])

    def test_snapshot_rotate_weekly(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('planb-20200612T0012Z')
        dataset.snapshot_create('planb-20200613T0019Z')
        dataset.snapshot_create('planb-20200614T0005Z')
        dataset.snapshot_create('planb-20200615T0004Z')
        dataset.snapshot_create('planb-20200616T0014Z')
        dataset.snapshot_create('planb-20200617T0004Z')
        dataset.snapshot_create('planb-20200618T0008Z')
        dataset.snapshot_create('planb-20200619T0004Z')
        dataset.snapshot_create('planb-20200620T0012Z')
        dataset.snapshot_create('planb-20200621T0004Z')
        dataset.snapshot_create('planb-20200622T0014Z')
        dataset.snapshot_rotate(retention_map={'w': 4})
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200612T0012Z', 'planb-20200614T0005Z',
            'planb-20200621T0004Z', 'planb-20200622T0014Z',
        ])

    def test_snapshot_rotate_yearly(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('planb-20190101T0012Z')
        dataset.snapshot_create('planb-20190201T0024Z')
        dataset.snapshot_create('planb-20191231T0008Z')
        dataset.snapshot_create('planb-20200102T0014Z')
        dataset.snapshot_rotate(retention_map={'y': 5})
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20190101T0012Z', 'planb-20191231T0008Z',
            'planb-20200102T0014Z',
        ])

    def test_snapshot_rotate_irregular(self):
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('planb-20200102T0912Z')
        dataset.snapshot_create('planb-20200102T1812Z')
        dataset.snapshot_create('planb-20200502T1743Z')
        dataset.snapshot_create('planb-20200503T1801Z')
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200102T0912Z', 'planb-20200102T1812Z',
            'planb-20200502T1743Z', 'planb-20200503T1801Z',
        ])
        destroyed = dataset.snapshot_rotate(retention_map={'h': 2})
        self.assertEqual(destroyed, ['planb-20200102T0912Z'])

    def test_snapshot_rotate_migration(self):
        # Test how the migration from multiple snapshots per backuprun to a
        # single snapshot will remove redundant snapshots.
        storage = self.get_dummy_storage()
        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.snapshot_create('planb-20180531T0543Z')
        dataset.snapshot_create('planb-20190518T0010Z')
        dataset.snapshot_create('planb-20190601T0002Z')
        dataset.snapshot_create('planb-20190619T0001Z')
        dataset.snapshot_create('planb-20190719T0002Z')
        dataset.snapshot_create('planb-20190819T0002Z')
        dataset.snapshot_create('planb-20190919T0002Z')
        dataset.snapshot_create('planb-20191019T0002Z')
        dataset.snapshot_create('planb-20191119T0002Z')
        dataset.snapshot_create('planb-20191219T2303Z')
        dataset.snapshot_create('planb-20200120T2303Z')
        dataset.snapshot_create('planb-20200221T2303Z')
        dataset.snapshot_create('planb-20200322T2302Z')
        dataset.snapshot_create('planb-20200418T2202Z')
        dataset.snapshot_create('planb-20200424T0906Z')
        dataset.snapshot_create('planb-20200425T2250Z')
        dataset.snapshot_create('planb-20200425T2249Z')
        dataset.snapshot_create('planb-20200426T2300Z')
        dataset.snapshot_create('planb-20200427T2228Z')
        dataset.snapshot_create('planb-20200428T2225Z')
        dataset.snapshot_create('planb-20200429T2212Z')
        dataset.snapshot_create('planb-20200430T2211Z')
        dataset.snapshot_create('planb-20200501T2211Z')
        dataset.snapshot_create('planb-20200502T2209Z')
        dataset.snapshot_create('planb-20200503T2209Z')
        dataset.snapshot_create('planb-20200503T2210Z')
        dataset.snapshot_create('planb-20200504T2209Z')
        dataset.snapshot_create('planb-20200505T2208Z')
        dataset.snapshot_create('planb-20200506T2205Z')
        dataset.snapshot_create('planb-20200507T2205Z')
        dataset.snapshot_create('planb-20200508T2204Z')
        dataset.snapshot_create('planb-20200509T2203Z')
        dataset.snapshot_create('planb-20200510T2206Z')
        destroyed = dataset.snapshot_rotate(
            {'y': 2, 'm': 12, 'w': 4, 'd': 16})
        self.assertEqual(destroyed, [
            'planb-20200503T2209Z',
            'planb-20200425T2249Z',
            'planb-20180531T0543Z',
        ])

    def test_zfs_storage(self):
        config = {
            'NAME': 'Zfs Storage', 'POOLNAME': 'tank', 'SUDOBIN': '/bin/echo'}
        ZfsStorage.ensure_defaults(config)
        storage = ZfsStorage(config, alias='zfs')

        with patch.object(storage, '_perform_binary_command') as m, \
                TemporaryDirectory() as tmpdir1, \
                TemporaryDirectory() as tmpdir2:
            # The dataset will be created if it doesn't already exist.
            m.side_effect = [
                '',  # ensure_exists: get mountpoint
                'filesystem',  # zfs_create: get dataset type tank
                # zfs_create: get dataset type tank/my_dataset
                CalledProcessError(
                    1, 'cmd', 'stdout',
                    'cannot open my_dataset: dataset does not exist'),
                '',  # zfs_create: create dataset and set opts
                '',  # zfs_create: mount dataset
                tmpdir1,  # zfs_create: get mountpoint
                tmpdir1,  # ensure_exists: get data path
                '',  # ensure_exists: unmount
            ]
            dataset = storage.get_dataset('tank/my_dataset')
            dataset.set_dataset_type('filesystem', 'data')  # default
            dataset.ensure_exists()
            m.assert_any_call((
                'create', '-o', 'canmount=noauto', 'tank/my_dataset'))

            # When a dataset is worked on it will become the workdirectory.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                tmpdir1,  # begin_work: get mountpoint
                '',  # begin_work: mount
                '',  # end_work: unmount
            ]
            with dataset.workon():
                self.assertEqual(os.getcwd(), dataset.get_data_path())

            # Test dataset rename command sequence and attribute updates.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                tmpdir1,  # rename_dataset: get mountpoint
                '',       # rename_dataset: rename dataset
                tmpdir2,  # rename_dataset: get new mountpoint
            ]
            with patch('os.mkdir') as mock_mkdir, \
                    patch('os.rmdir') as mock_rmdir:
                # Rename: checks mountpoints, does rename, makes dir
                dataset.rename_dataset('tank/new_name')
                mock_rmdir.assert_called_once_with(tmpdir1)
                mock_mkdir.assert_called_once_with(tmpdir2)

            m.assert_any_call(
                ('rename', 'tank/my_dataset', 'tank/new_name'))
            self.assertEqual(dataset.name, 'tank/new_name')

            # When a dataset is renamed zfs will unmount it so the workdir
            # must fall outside the dataset. So we cannot workon and rename the
            # same dataset.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                tmpdir1,    # begin_work: get mountpoint
                '',         # begin_work: mount
                tmpdir1,    # rename_dataset: get mountpoint
                '',         # rename_dataset: rename
                tmpdir2,    # rename_dataset: get mountpoint
                '',         # end_work: unmount
            ]
            with self.assertRaises(AssertionError):
                with dataset.workon(), \
                        patch('os.mkdir') as mock_mkdir, \
                        patch('os.rmdir') as mock_rmdir:
                    dataset.rename_dataset('tank/other_name')
                    mock_rmdir.assert_called_once_with(tmpdir1)
                    mock_mkdir.assert_called_once_with(tmpdir2)

            # Dataset listing.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                'tank/new_name\t101\tfilesystem\t-',  # get_datasets: list
            ]
            datasets = storage.get_datasets()
            self.assertEqual(len(datasets), 1)
            self.assertEqual(datasets[0].name, 'tank/new_name')
            self.assertEqual(datasets[0].disk_usage, 101)
            m.assert_any_call((
                'list', '-r', '-t', 'filesystem,volume',
                '-Hpo', 'name,used,type,planb:contains', config['POOLNAME']))
