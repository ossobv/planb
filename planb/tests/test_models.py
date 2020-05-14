import datetime

from django.test import TestCase, override_settings
from mock import patch

from planb.factories import FilesetFactory


class PlanbTestCase(TestCase):
    def test_rename_fileset(self):
        fileset = FilesetFactory(storage_alias='zfs')
        old_name = fileset.storage.name_dataset(
            fileset.hostgroup.name, fileset.friendly_name)
        self.assertEqual(old_name, fileset.dataset_name)
        fileset.hostgroup.name = 'some-other'
        fileset.hostgroup.save()
        new_name = fileset.storage.name_dataset(
            fileset.hostgroup.name, fileset.friendly_name)
        with patch.object(
                fileset.storage._storage, '_perform_binary_command') as m:
            m.return_value = '/' + old_name  # dataset mountpoint.
            fileset.rename_dataset(new_name)
            m.assert_called_with(('rename', old_name, new_name))
        self.assertEqual(fileset.dataset_name, new_name)

    def test_snapshot_create(self):
        fileset = FilesetFactory(storage_alias='dummy')
        with patch('planb.models.datetime') as m:
            m.utcnow.return_value = datetime.datetime(2020, 5, 3, 14, 42)
            # snapshot create will always create a snapshot.
            # The snapshot name is a utc timestamp.
            self.assertEqual(
                fileset.snapshot_create(), 'planb-20200503T1442Z')
            m.utcnow.return_value = datetime.datetime(2020, 5, 3, 15, 31)
            self.assertEqual(
                fileset.snapshot_create(), 'planb-20200503T1531Z')

    @override_settings(PLANB_BLACKLIST_HOURS='9-17')
    @patch('planb.models.timezone')
    def test_blacklist_hours(self, timezone):
        # Using system defaults.
        fileset = FilesetFactory()
        self.assertEqual(fileset.get_blacklist_hours(), '9-17')
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 8, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 9, 42)
        self.assertTrue(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 17, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        # Using hostgroup defaults.
        fileset = FilesetFactory(hostgroup__blacklist_hours='11-14')
        self.assertEqual(fileset.get_blacklist_hours(), '11-14')
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 9, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 13, 42)
        self.assertTrue(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 14, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        # Using fileset settings, for example to skip during some heavy tasks.
        fileset = FilesetFactory(
            blacklist_hours='2,9-17', hostgroup__blacklist_hours='11-14')
        self.assertEqual(fileset.get_blacklist_hours(), '2,9-17')
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 1, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 2, 42)
        self.assertTrue(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 8, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 11, 42)
        self.assertTrue(fileset.is_in_blacklist_hours)
        timezone.now.return_value = datetime.datetime(2020, 5, 3, 17, 42)
        self.assertFalse(fileset.is_in_blacklist_hours)

    @override_settings(PLANB_RETENTION='7d,3w,6m')
    def test_retention(self):
        # Using system defaults.
        fileset = FilesetFactory()
        self.assertEqual(fileset.retention_map, {'m': 6, 'w': 3, 'd': 7})
        # Using hostgroup defaults.
        fileset = FilesetFactory(hostgroup__retention='12m,3y')
        self.assertEqual(fileset.retention_map, {'m': 12, 'y': 3})
        # Using fileset settings.
        fileset = FilesetFactory(
            retention='36h,7d', hostgroup__retention='12m,3y')
        self.assertEqual(fileset.retention_map, {'h': 36, 'd': 7})
