from django.test import TestCase
from mock import patch

from planb.factories import FilesetFactory


class PlanbTestCase(TestCase):
    def test_rename_fileset(self):
        fileset = FilesetFactory(storage_alias='zfs')
        old_name = fileset.storage.get_dataset_name(
            fileset.hostgroup.name, fileset.friendly_name)
        self.assertEqual(old_name, fileset.dataset_name)
        fileset.hostgroup.name = 'some-other'
        fileset.hostgroup.save()
        new_name = fileset.storage.get_dataset_name(
            fileset.hostgroup.name, fileset.friendly_name)
        with patch.object(fileset.storage, '_perform_binary_command') as m:
            m.return_value = '/' + old_name  # dataset mountpoint.
            fileset.rename_dataset(new_name)
            m.assert_called_with(('rename', old_name, new_name))
        self.assertEqual(fileset.dataset_name, new_name)

    def test_snapshot_create(self):
        fileset = FilesetFactory(storage_alias='dummy')
        # Clean dataset, create all enabled snapshots.
        self.assertEqual(len(fileset.snapshot_create()), 4)
        # Snapshots exist, still create a new daily.
        self.assertEqual(len(fileset.snapshot_create()), 1)

        fileset = FilesetFactory(
            storage_alias='dummy', monthly_retention=False,
            yearly_retention=False)
        # Only create the daily and weekly snapshots.
        self.assertEqual(len(fileset.snapshot_create()), 2)
        self.assertEqual(len(fileset.snapshot_create()), 1)
