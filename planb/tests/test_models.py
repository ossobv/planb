import datetime
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from planb.factories import FilesetFactory
from planb.tests.base import PlanbTestCase


class PlanbTestCase(PlanbTestCase):
    def test_rename_fileset(self):
        fileset = FilesetFactory(storage_alias='zfs')
        old_name = fileset.storage.name_dataset(
            fileset.hostgroup.name, fileset.friendly_name)
        self.assertEqual(old_name, fileset.dataset_name)
        fileset.hostgroup.name = 'some-other'
        fileset.hostgroup.save()
        new_name = fileset.storage.name_dataset(
            fileset.hostgroup.name, fileset.friendly_name)

        with patch.object(fileset.storage._storage,
                          '_perform_binary_command') as mock_zfs, \
                patch('os.mkdir') as mock_mkdir, \
                patch('os.rmdir') as mock_rmdir:
            mock_zfs.side_effect = [
                '/' + old_name,     # get dataset mountpoint
                '',                 # rename
                '/' + new_name,     # get dataset mountpoint
            ]

            # Rename: checks mountpoints, does rename, makes dir
            fileset.rename_dataset(new_name)

            mock_zfs.assert_any_call(
                ('get', '-Ho', 'value', 'mountpoint', old_name))
            mock_zfs.assert_any_call(
                ('rename', old_name, new_name))
            mock_zfs.assert_any_call(
                ('get', '-Ho', 'value', 'mountpoint', new_name))

            mock_rmdir.assert_called_with('/' + old_name)  # old mountpoint
            mock_mkdir.assert_called_with('/' + new_name)  # new mountpoint

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

    @override_settings(PLANB_RETENTION='')
    def test_should_backup(self):
        fileset = FilesetFactory(
            average_duration=19172, first_fail=timezone.now(),
            is_enabled=False)
        # Fileset disabled.
        self.assertFalse(fileset.should_backup())
        fileset.is_enabled = True

        # Last backup failed.
        self.assertTrue(fileset.should_backup())
        fileset.first_fail = None

        # No backups.
        self.assertTrue(fileset.should_backup())
        fileset.last_ok = timezone.now()

        # No retention = no backup interval.
        self.assertFalse(fileset.should_backup())
        fileset.retention = '3d'
        del fileset._retention_map

        # Has recent backup.
        self.assertFalse(fileset.should_backup())

        # No recent backup.
        # 68400 seconds since last + 19172 avg duration > 86400 daily interval.
        fileset.last_ok = timezone.now() - datetime.timedelta(hours=19)
        self.assertTrue(fileset.should_backup())

        # Daily backups should start each day, hourly backups should start
        # each hour, regardless of the time since the previous backup.
        with patch('planb.models.timezone') as m:
            # Time since last backup is just 5 hours.
            fileset.last_ok = datetime.datetime(
                2020, 5, 19, 19, tzinfo=timezone.utc)
            m.now.return_value = datetime.datetime(
                2020, 5, 20, tzinfo=timezone.utc)
            self.assertTrue(fileset.should_backup())
            # Same day of the month, one month later at midnight.
            m.now.return_value = datetime.datetime(
                2020, 6, 19, tzinfo=timezone.utc)
            self.assertTrue(fileset.should_backup())

        # Backup is running.
        # The running status is refreshed from the database.
        fileset.is_running = True
        fileset.save()
        self.assertFalse(fileset.should_backup())
        fileset.is_running = False
        fileset.save()
        self.assertTrue(fileset.should_backup())
