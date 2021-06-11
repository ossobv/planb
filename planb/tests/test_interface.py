from django.template import Context, Template
from django.utils import timezone

from planb.factories import (
    BackupRunFactory, FilesetFactory, HostGroupFactory, UserFactory)
from planb.models import BOGODATE
from planb.tests.base import PlanbTestCase


class InterfaceTestCase(PlanbTestCase):
    def test_admin_model(self):
        user = UserFactory(is_staff=True, is_superuser=True)
        self.client.force_login(user)

        hostgroup = HostGroupFactory()
        fileset = FilesetFactory(hostgroup=hostgroup)
        backuprun = BackupRunFactory(fileset=fileset)

        response = self.client.get('/planb/hostgroup/')
        row = response.context['results'][0]
        self.assertIn(hostgroup.name, row[1])
        self.assertIn(fileset.friendly_name, row[5])

        response = self.client.get('/planb/fileset/')
        row = response.context['results'][0]
        self.assertIn(fileset.friendly_name, row[1])

        response = self.client.get('/planb/backuprun/')
        row = response.context['results'][0]
        self.assertIn(str(backuprun.fileset), row[2])

        # Test enqueue admin action.
        data = {
            'action': 'enqueue_multiple',
            '_selected_action': [fileset.pk],
        }
        response = self.client.post('/planb/fileset/', data, follow=True)
        self.assertRedirects(response, '/planb/fileset/')
        self.assertContains(
            response, 'The selection has been queued for immediate backup')

        # Test rename task spawn after hostgroup name change.
        data = {
            'name': 'my-group',
            '_save': 'Save',
        }
        response = self.client.post(
            '/planb/hostgroup/{}/change/'.format(hostgroup.pk), data,
            follow=True)
        self.assertContains(
            response,
            'A rename task has been queued for all filesets in the hostgroup')

        # Test rename task spawn after fileset name change.
        data = {
            'friendly_name': 'my-host',
            'hostgroup': hostgroup.pk,
        }
        response = self.client.post(
            '/planb/fileset/{}/change/'.format(fileset.pk), data, follow=True)
        self.assertContains(
            response, 'A rename task has been queued for the fileset')

    def test_global_messages_templatetag(self):
        context = Context()
        template = Template('{% load planb %}{% global_messages %}')

        self.assertEqual(template.render(context), '')

        # Hack to trigger email updates doesn't show messages.
        FilesetFactory(first_fail=BOGODATE)

        self.assertEqual(template.render(context), '')

        first_fail = timezone.now()
        hostgroup = HostGroupFactory()
        for i in range(3):
            fileset = FilesetFactory(
                hostgroup=hostgroup, first_fail=first_fail)
            BackupRunFactory(fileset=fileset)

        self.assertEqual(
            template.render(context).count('Backup failure since'), 3)

        for i in range(8):
            fileset = FilesetFactory(
                hostgroup=hostgroup, first_fail=first_fail)
            BackupRunFactory(fileset=fileset)

        output = template.render(context)
        self.assertIn(
            'There are lots of failed backups. Listing only the oldest 10.',
            output)
        self.assertEqual(output.count('Backup failure since'), 10)
