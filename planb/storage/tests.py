import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from planb.common.subprocess2 import CalledProcessError
from planb.storage import load_storage_pools
from planb.storage.dummy import DummyStorage
from planb.storage.zfs import ZfsStorage


class PlanbStorageTestCase(TestCase):
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
        dataset.snapshot_create('daily-202005021743')
        dataset.snapshot_create('daily-202005031801')
        dataset.snapshot_create('daily-202005041602')
        dataset.snapshot_create('hello')
        dataset.snapshot_create('planb-20200102T0912Z')
        dataset.snapshot_create('planb-20200504T1458Z')
        dataset.snapshot_create('planb-20200504T1655Z')
        dataset.snapshot_create('planb-20200504T1700Z')
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200102T0912Z', 'daily-202005021743', 'daily-202005031801',
            'daily-202005041602', 'planb-20200504T1458Z',
            'planb-20200504T1655Z', 'planb-20200504T1700Z', 'hello',
        ])
        destroyed = dataset.snapshot_rotate(
            retention_map={'h': 2, 'y': 1})
        self.assertEqual(
            destroyed, ['planb-20200504T1655Z', 'daily-202005031801',
                        'daily-202005021743'])
        self.assertEqual(dataset.snapshot_list(), [
            'planb-20200102T0912Z', 'daily-202005041602',
            'planb-20200504T1458Z', 'planb-20200504T1700Z', 'hello',
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
        dataset.snapshot_create('yearly-201805310543')
        dataset.snapshot_create('monthly-201905180010')
        dataset.snapshot_create('yearly-201906010002')
        dataset.snapshot_create('monthly-201906190001')
        dataset.snapshot_create('monthly-201907190002')
        dataset.snapshot_create('monthly-201908190002')
        dataset.snapshot_create('monthly-201909190002')
        dataset.snapshot_create('monthly-201910190002')
        dataset.snapshot_create('monthly-201911190002')
        dataset.snapshot_create('monthly-201912192303')
        dataset.snapshot_create('monthly-202001202303')
        dataset.snapshot_create('monthly-202002212303')
        dataset.snapshot_create('monthly-202003222302')
        dataset.snapshot_create('weekly-202004182202')
        dataset.snapshot_create('monthly-202004240906')
        dataset.snapshot_create('daily-202004252250')
        dataset.snapshot_create('weekly-202004252249')
        dataset.snapshot_create('daily-202004262300')
        dataset.snapshot_create('daily-202004272228')
        dataset.snapshot_create('daily-202004282225')
        dataset.snapshot_create('daily-202004292212')
        dataset.snapshot_create('daily-202004302211')
        dataset.snapshot_create('daily-202005012211')
        dataset.snapshot_create('daily-202005022209')
        dataset.snapshot_create('daily-202005032209')
        dataset.snapshot_create('weekly-202005032209')
        dataset.snapshot_create('daily-202005042209')
        dataset.snapshot_create('daily-202005052208')
        dataset.snapshot_create('daily-202005062205')
        dataset.snapshot_create('daily-202005072205')
        dataset.snapshot_create('daily-202005082204')
        dataset.snapshot_create('daily-202005092203')
        dataset.snapshot_create('daily-202005102206')
        destroyed = dataset.snapshot_rotate(
            {'y': 2, 'm': 12, 'w': 4, 'd': 16})
        self.assertEqual(destroyed, [
            'daily-202005032209', 'weekly-202004252249', 'yearly-201805310543',
        ])

    def test_zfs_storage(self):
        config = {
            'NAME': 'Zfs Storage', 'POOLNAME': 'tank', 'SUDOBIN': '/bin/echo'}
        ZfsStorage.ensure_defaults(config)
        storage = ZfsStorage(config, alias='zfs')

        with patch.object(storage, '_perform_binary_command') as m, \
                TemporaryDirectory() as tmpdir:
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
                tmpdir,  # zfs_create: get mountpoint
                tmpdir,  # ensure_exists: get data path
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
                tmpdir,  # begin_work: get mountpoint
                '',  # begin_work: mount
                '',  # end_work: unmount
            ]
            with dataset.workon():
                self.assertEqual(os.getcwd(), dataset.get_data_path())

            # Test dataset rename command sequence and attribute updates.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                tmpdir,  # rename_dataset: get mountpoint
                '',  # rename_dataset: rename dataset
            ]
            dataset.rename_dataset('tank/new_name')
            m.assert_any_call(
                ('rename', 'tank/my_dataset', 'tank/new_name'))
            self.assertEqual(dataset.name, 'tank/new_name')

            # When a dataset is renamed zfs will unmount it so the workdir
            # must fall outside the dataset. So we cannot workon and rename the
            # same dataset.
            m.reset_mock(side_effect=True)
            m.side_effect = [
                tmpdir,  # begin_work: get mountpoint
                '',  # begin_work: mount
                tmpdir,  # rename_dataset: get mountpoint
                '',  # end_work: unmount
            ]
            with self.assertRaises(AssertionError):
                with dataset.workon():
                    dataset.rename_dataset('tank/other_name')

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
                'list', '-d', '1', '-t', 'filesystem,volume',
                '-Hpo', 'name,used,type,planb:contains', config['POOLNAME']))
