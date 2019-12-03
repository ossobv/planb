from django.test import TestCase
from mock import patch

from .factories import FilesetFactory


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
