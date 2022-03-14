import json
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import override_settings

from planb.factories import (
    BackupRunFactory, FilesetFactory, HostGroupFactory)
from planb.models import Fileset
from planb.storage.dummy import DummyStorage
from planb.tests.base import PlanbTestCase
from planb.transport_exec.factories import ExecConfigFactory
from planb.transport_rsync.factories import RsyncConfigFactory


class CommandTestCase(PlanbTestCase):
    maxDiff = None

    def run_command(self, *args, **kwargs):
        stdout, stderr = StringIO(), StringIO()
        call_command(
            *args, **kwargs, no_color=True, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_bclone(self):
        fileset = FilesetFactory()
        RsyncConfigFactory(fileset=fileset)
        stdout, stderr = self.run_command(
            'bclone', fileset.pk, 'fileset-clone', 'copy.host.co')
        fileset_copy = Fileset.objects.get(friendly_name='fileset-clone')
        # Note that the output also contains a backup queue message.
        self.assertIn(
            'Cloned {} to {}'.format(fileset, fileset_copy), stdout)
        self.assertEqual(fileset_copy.get_transport().host, 'copy.host.co')

    def test_blist(self):
        stdout, stderr = self.run_command('blist')
        self.assertEqual(stdout, '\n')

        hostgroup = HostGroupFactory(name='local')
        web01 = FilesetFactory(friendly_name='web01', hostgroup=hostgroup)
        db01 = FilesetFactory(friendly_name='db01', hostgroup=hostgroup)

        stdout, stderr = self.run_command('blist', zabbix=True)
        decoded = json.loads(stdout)
        for item in decoded:
            assert item.get('{#PLANB}'), item
            item['{#PLANB}'] = '$host'
        expected = [
            {'{#ID}': db01.pk, '{#NAME}': 'local-db01', '{#PLANB}': '$host'},
            {'{#ID}': web01.pk, '{#NAME}': 'local-web01', '{#PLANB}': '$host'},
        ]
        self.assertEqual(expected, decoded)

        ExecConfigFactory(fileset=web01, transport_command='/bin/magic')
        RsyncConfigFactory(fileset=db01, host='database1.local')
        FilesetFactory(friendly_name='stats', hostgroup__name='remote')
        stdout, stderr = self.run_command('blist')
        self.assertEqual(stdout, TEST_BLIST)

    def test_bqcluster(self):
        stdout, stderr = self.run_command(
            'bqcluster', queue='test', run_once=True)
        self.assertIn("Starting qcluster for queue 'test'", stdout)

    def test_bqueue(self):
        # The task queue may have other test data, clean it up.
        self.run_command('bqueueflush')

        fileset = FilesetFactory()
        stdout, stderr = self.run_command('bqueueall')
        self.assertIn('Enqueued {}'.format(fileset), stdout)
        stdout, stderr = self.run_command('bqueueflush')
        self.assertIn('Dropped 1 jobs from Task queue', stdout)
        self.assertIn('Dropped 1 jobs from DB queue', stdout)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_breport(self):
        fileset = FilesetFactory(
            friendly_name='desktop01.local',
            hostgroup__name='local', hostgroup__notify_email='test@local',
            total_size_mb=94950, last_ok='2019-11-29T13:47Z',
            last_run='2019-11-29T13:47Z')
        BackupRunFactory(
            fileset=fileset, success=True, total_size_mb=94950,
            snapshot_size_mb=84950, snapshot_size_listing=TEST_DUTREE_LISTING)

        stdout, stderr = self.run_command('breport', output='email')
        message = mail.outbox[0]
        self.assertEqual(message.to, ['test@local'])
        self.assertEqual(
            message.subject, 'Example Company backup report "local"')
        self.assertEqual(message.body, TEST_BREPORT)
        attachment = message.attachments[0]
        self.assertEqual(attachment[0], 'pretty_report.html')
        self.assertIn(
            '<title>PlanB backup report for &quot;local&quot;</title>',
            attachment[1])
        self.assertEqual(attachment[2], 'text/html')

    def test_confexport(self):
        fileset = FilesetFactory(
            friendly_name='desktop', hostgroup__name='local')
        RsyncConfigFactory(fileset=fileset)
        stdout, stderr = self.run_command('confexport', output='json')
        self.assertEqual(stdout, TEST_CONFEXPORT_JSON)
        stdout, stderr = self.run_command('confexport', output='yaml')
        self.assertEqual(stdout, TEST_CONFEXPORT_YAML)

    def test_slist(self):
        # Create a standalone clean dummy storage for this test.
        storage = DummyStorage({'NAME': 'DummyPool I'}, 'dummy')
        test_pools = {'dummy': storage}
        with \
                patch(
                    'planb.management.commands.slist.storage_pools',
                    test_pools), \
                patch('planb.models.storage_pools', test_pools):
            dataset = FilesetFactory(
                friendly_name='storage', hostgroup__name='local',
                storage_alias='dummy').get_dataset()
            dataset.set_disk_usage(84883399164)
            dataset = FilesetFactory(
                friendly_name='desktop', hostgroup__name='local',
                storage_alias='dummy').get_dataset()
            dataset.set_disk_usage(60630999402)
            # a dataset not mapped to a fileset.
            dataset = storage.get_dataset('cold/other_host')
            dataset.set_leaf(True)
            dataset.set_disk_usage(271626877324)
            stdout, stderr = self.run_command('slist')
            self.assertEqual(stdout, TEST_SLIST)


TEST_BLIST = '''[local]
db01                            rsync transport database1.local
web01                           exec transport /bin/magic

[remote]
stats                           MISSING_TRANSPORT

'''


TEST_CONFEXPORT_JSON = '''/* local-desktop */

{
  "retention": {
    "hourly": 0,
    "daily": 16,
    "weekly": 4,
    "monthly": 12,
    "yearly": 2
  },
  "schedule": "nightly",
  "paths": {
    "root": "/",
    "include": [
      "data",
      "etc",
      "home",
      "root",
      "srv",
      "usr/local/bin",
      "var/backups",
      "var/lib/dpkg/status*",
      "var/lib/psdiff.db*",
      "var/log/auth*",
      "var/spool/cron",
      "var/www"
    ],
    "exclude": []
  },
  "notes": ""
}

'''

TEST_CONFEXPORT_YAML = '''---
# local-desktop

retention:
  hourly: 0
  daily: 16
  weekly: 4
  monthly: 12
  yearly: 2
schedule: nightly
paths:
  root: /
  include:
    - data
    - etc
    - home
    - root
    - srv
    - usr/local/bin
    - var/backups
    - "var/lib/dpkg/status*"
    - "var/lib/psdiff.db*"
    - "var/log/auth*"
    - var/spool/cron
    - var/www
  exclude: []
notes: ""

'''


TEST_DUTREE_LISTING = '''\
/.local/share/baloo/index: 13,719,351,296
/.local/share/*: 7,878,635,520
"/.steam/steam/steamapps/common/Left 4 Dead 2/left4dead2/": 7,099,121,664
"/.steam/steam/steamapps/common/Left 4 Dead 2/*": 6,514,282,496
/.steam/steam/steamapps/common/*: 5,675,253,760
"/Downloads/": 5,122,678,784
"/Music/": 13,076,504,576
"/Pictures/": 6,761,598,976
/dev/: 6,166,724,608
/download/: 5,009,948,672
/*: 9,679,134,720'''


TEST_SLIST = '''; (nogroup)
; (when purging, do not forget to remove encryption keys from zfskeys dir)
; (see planb-zfskeys-check contrib tool)
cold/other_host                                         253.0 GB  id=NONE

; local
local-desktop                                            56.5 GB  id=2
local-storage                                            79.1 GB  id=1

'''


TEST_BREPORT = '''\
PlanB backup report for "local"
===============================

The following report contains a listing of all PlanB based backups made
by Example Company. Please take a moment to examine its correctness:

- Are all hosts you want backed up listed?
- Are the paths you want included all mentioned?
- Do you wish to change the retention (snapshot count) for a host?

For your convenience, the paths which take up the most disk space are
listed as well. At your request, we can add paths to exclude from the
backups.

*NOTE: The data sizes mentioned in this report are a snapshot. Sizes on
your final invoice may differ. All numbers in this report use binary
prefixes:* 1 GB = 2\\ :sup:`30`

The following hosts are backed up using the Example Company PlanB
backup service.

+---------------------------------+-------------+-------+------------+
| name                            | disk use    | eff.  | last back. |
+=================================+=============+=======+============+
| desktop01.local                 |   92.7 GB🥇 | N/A🥇 | 2019-11-29 |
+---------------------------------+-------------+-------+------------+
| **Total**                       |   92.7 GB   |       |            |
+---------------------------------+-------------+-------+------------+

----------------------
Reports per host below
----------------------

+------------------------------------------------------------------------------+
| **desktop01.local**                                                          |
+========================+=====================================================+
| Total size             | 92.7 GB (0 snapshots)                               |
+------------------------+-----------------------------------------------------+
| Last snapshot size     | 83.0 GB (N/A efficiency)                            |
+------------------------+-----------------------------------------------------+
| Last successful backup | 2019-11-29 14:47:00                                 |
+------------------------+-----------------------------------------------------+
| Average run time       | 0s                                                  |
+------------------------+-----------------------------------------------------+
| Configured retention   | 16 days, 4 weeks, 12 months, 2 years                |
+------------------------+-----------------------------------------------------+
| Use double backup      | NO                                                  |
+------------------------+-----------------------------------------------------+



Last snapshot disk usage:

-    12.8 GB ``/.local/share/baloo/index``
-     7.3 GB ``/.local/share/*``
-     6.6 GB ``/.steam/steam/steamapps/common/Left 4 Dead 2/left4dead2/``
-     6.1 GB ``/.steam/steam/steamapps/common/Left 4 Dead 2/*``
-     5.3 GB ``/.steam/steam/steamapps/common/*``
-     4.8 GB ``/Downloads/``
-    12.2 GB ``/Music/``
-     6.3 GB ``/Pictures/``
-     5.7 GB ``/dev/``
-     4.7 GB ``/download/``
-     9.0 GB ``/*``

Available snapshots:


Warning: there are no snapshots available for this host.


| --
| PlanB, the Example Company backup service
| Please contact support@example.com if anything is amiss

'''  # noqa
