import os
from tempfile import TemporaryDirectory

from django.test import TestCase
from mock import patch

from planb.common.subprocess2 import CalledProcessError
from planb.storage import pools


class PlanbStorageTestCase(TestCase):
    def test_dummy_storage(self):
        storage = pools['dummy']

        dataset = storage.get_dataset('my_dataset')
        dataset.ensure_exists()
        dataset.rename_dataset('new_name')
        self.assertEqual(dataset.name, 'new_name')

        datasets = storage.get_datasets()
        self.assertEqual(len(datasets), 1)
        self.assertEqual(datasets[0].name, 'new_name')

    def test_zfs_storage(self):
        storage = pools['zfs']

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
                '',  # zfs_create: create dataset
                '',  # zfs_create: set dataset opts
                '',  # zfs_create: mount dataset
                tmpdir,  # zfs_create: get mountpoint
                tmpdir,  # ensure_exists: get data path
                '',  # ensure_exists: unmount
            ]
            dataset = storage.get_dataset('tank/my_dataset')
            dataset.ensure_exists()
            m.assert_any_call(('create', 'tank/my_dataset'))

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
                'tank/new_name\t101',  # get_datasets: list
            ]
            datasets = storage.get_datasets()
            self.assertEqual(len(datasets), 1)
            self.assertEqual(datasets[0].name, 'tank/new_name')
            self.assertEqual(datasets[0].disk_usage, 101)
            m.assert_any_call(('list', '-Hpo', 'name,used'))
