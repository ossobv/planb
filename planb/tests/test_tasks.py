from contextlib import contextmanager
import datetime
from unittest.mock import Mock, patch

from django.test import override_settings
from django.utils.timezone import make_aware

from planb.factories import BackupRunFactory, FilesetFactory
from planb.models import Fileset
from planb.tasks import (
    FilesetRunner, conditional_run, dutree_run, finalize_run, manual_run,
    rename_run, unconditional_run)
from planb.signals import backup_done
from planb.tests.base import PlanbTestCase
from planb.transport_rsync.factories import RsyncConfigFactory

RSYNC_BIN = '/not/rsync'


def message(
        *args, message_fmt='{level}:{module}:[{}] {}', level='INFO',
        module='planb.tasks'):
    return message_fmt.format(*args, level=level, module=module)


# These tests use the dummy storage because zfs is complicated to mock and
# not the focus of this test.
@override_settings(PLANB_RSYNC_BIN=RSYNC_BIN)
class TaskTestCase(PlanbTestCase):
    maxDiff = 8192

    def test_runner(self):
        fileset = FilesetFactory()
        runner = FilesetRunner(fileset.pk)
        self.assertFalse(runner._fileset_lock.is_acquired())

        with FilesetRunner(fileset.pk) as runner:
            self.assertTrue(runner._fileset_lock.is_acquired())

    def test_conditional_run(self):
        fileset = FilesetFactory(storage_alias='dummy')
        # Conditional run will only run backup tasks outside work hours.
        # XXX: we should move the use of timezones to a more central
        # location probably.. this m1, m2 is not nice
        with patch('planb.models.timezone') as m1, \
                patch('planb.tasks.timezone') as m2, \
                self.assertLogs('planb.tasks', level='INFO') as log:
            m1.now.return_value = datetime.datetime(2019, 1, 1, 11, 0)
            m2.now.return_value = datetime.datetime(2019, 1, 1, 11, 0)
            conditional_run(fileset.pk)
            self.assertEqual(
                log.output,
                [message(fileset, 'Skipped because of blacklist hours: 9-17')])

        RsyncConfigFactory(fileset=fileset)
        # Outside work hours it will immediatly run the backup.
        with patch('planb.models.timezone') as m, \
                patch('planb.transport_rsync.models.check_output') as c:
            m.now.return_value = make_aware(
                datetime.datetime(2019, 1, 1, 3, 0))
            conditional_run(fileset.pk)
            call = c.call_args[0][0]
            self.assertEqual(call[0], RSYNC_BIN)
            self.assertEqual(call[-1], fileset.get_dataset().get_data_path())

    def test_manual_run_is_running(self):
        fileset = FilesetFactory(storage_alias='dummy', is_running=True)
        # Manual run does nothing if the fileset is marked as running.
        with self.assertLogs('planb.tasks', level='INFO') as log, \
                patch('planb.transport_rsync.models.check_output') as c:
            manual_run(fileset.pk, custom_snapname=None)
            self.assertEqual(
                log.output,
                [message(fileset, 'Manually requested backup')])
            # If the fileset is marked as running manual_run does nothing.
            c.assert_not_called()

    def test_manual_run(self):
        self.manual_run_on_fileset(FilesetFactory(storage_alias='dummy'))

    def test_manual_run_on_disabled_fileset(self):
        self.manual_run_on_fileset(
            FilesetFactory(storage_alias='dummy', is_enabled=False))

    def manual_run_on_fileset(self, fileset):
        RsyncConfigFactory(fileset=fileset)
        # Otherwise manual run will immediatly run the backup.
        with self.assertLogs('planb.tasks', level='INFO') as log, \
                patch('planb.transport_rsync.models.check_output') as c:
            manual_run(fileset.pk, custom_snapname=None)
            self.assertEqual(
                log.output, [
                    message(fileset, 'Manually requested backup'),
                    message(fileset, 'Starting backup'),
                    message(fileset, 'Completed successfully')])
            call = c.call_args[0][0]
            self.assertEqual(call[0], RSYNC_BIN)
            self.assertEqual(call[-1], fileset.get_dataset().get_data_path())

    def test_unconditional_run(self):
        fileset = FilesetFactory(storage_alias='dummy')
        RsyncConfigFactory(fileset=fileset)
        # Unconditional run will always run a backup.
        with self.assertLogs('planb.tasks', level='INFO') as log, \
                patch('planb.transport_rsync.models.check_output') as c:
            unconditional_run(fileset.pk)
            self.assertEqual(
                log.output, [
                    message(fileset, 'Starting backup'),
                    message(fileset, 'Completed successfully')])
            call = c.call_args[0][0]
            self.assertEqual(call[0], RSYNC_BIN)
            self.assertEqual(call[-1], fileset.get_dataset().get_data_path())

    def test_dutree_run(self):
        # Dutree is spawned at the end of the unconditional_run.
        fileset = FilesetFactory(storage_alias='dummy')
        # All successful backups have one snapshot and the dutree task only
        # spawns when do_snapshot_size_listing=True on the fileset.
        # This attribute is copied to the backuprun so it can run independent
        # of fileset changes made by the user.
        run = BackupRunFactory(
            fileset=fileset,
            attributes='do_snapshot_size_listing: true\nsnapshot: daily')
        with self.assertLogs('planb.tasks', level='INFO') as log:
            dutree_run(fileset.pk, run.pk)
            self.assertEqual(
                log.output, [
                    message(fileset, 'Starting dutree scan'),
                    message(fileset, 'Completed dutree scan')])
        # Previously a backup could create multiple snapshots.
        run = BackupRunFactory(
            fileset=fileset,
            attributes='do_snapshot_size_listing: true\nsnapshots:\n- daily')
        with self.assertLogs('planb.tasks', level='INFO') as log:
            dutree_run(fileset.pk, run.pk)
            self.assertEqual(
                log.output, [
                    message(fileset, 'Starting dutree scan'),
                    message(fileset, 'Completed dutree scan')])

    def test_rename_run(self):
        # The rename task checks if the path has changed since the task was
        # queued. If it has changed the rename is aborted.
        fileset = FilesetFactory()
        with self.assertLogs('planb.tasks', level='WARNING') as log:
            rename_run(fileset.pk, 'previous_name', 'new_name')
            self.assertEqual(
                log.output, [
                    message(
                        fileset,
                        'Fileset name to {!r} cancelled, dataset {!r} does '
                        'not match current {!r}'.format(
                            'new_name', 'previous_name', fileset.dataset_name),
                        level='WARNING'),
                    ])

        with self.assertLogs('planb.tasks', level='INFO') as log:
            rename_run(fileset.pk, fileset.dataset_name, 'new_name')
            self.assertEqual(
                log.output, [
                    message(
                        fileset, 'Rename from {!r} to {!r}'.format(
                            fileset.dataset_name, 'new_name')),
                    message(
                        fileset, 'Rename to {!r} complete'.format('new_name')),
                    ])
            fileset.refresh_from_db()
            self.assertEqual(fileset.dataset_name, 'new_name')

    @contextmanager
    def signal_handler(self, signal):
        handler = Mock()
        signal.connect(handler)
        yield handler
        signal.disconnect(handler)

    def test_finalize_run(self):
        # finalize_run is a hook that runs after the backup task any of manual,
        # conditional or unconditional. It receives the Task object and passes
        # the success and result flag to the FilesetRunner.
        # Afaik None of the tasks actually return anything.
        fileset = FilesetFactory()
        task = Mock(args=[fileset.pk], success=False, result=None)
        with self.assertLogs('planb.tasks', level='ERROR') as log, \
                self.signal_handler(backup_done) as handler:
            finalize_run(task)
            self.assertEqual(
                log.output, [
                    message(
                        fileset, 'Job run failure: {!r}'.format(None),
                        level='ERROR')])
            handler.assert_called_with(
                sender=Fileset, fileset=fileset, success=False,
                signal=backup_done)

        task = Mock(args=[fileset.pk], success=True, result=None)
        with self.assertLogs('planb.tasks', level='INFO') as log, \
                self.signal_handler(backup_done) as handler:
            finalize_run(task)
            self.assertEqual(log.output, [message(fileset, 'Done')])
            handler.assert_called_with(
                sender=Fileset, fileset=fileset, success=True,
                signal=backup_done)
