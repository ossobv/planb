from datetime import timedelta
from fnmatch import fnmatch
from subprocess import check_output

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.message import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from django.utils import timezone
from django.utils.translation import ugettext as _

from planb.models import HostConfig, HostGroup


class Command(BaseCommand):
    help = 'Create backup report and optionally e-mail'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', choices=('text', 'html', 'email'), default='text',
            help='What kind of output; option email sends the mail')
        parser.add_argument('--force', action='store_true', help=(
            'If output is email and it was sent recently, send anyway'))
        parser.add_argument('--with-disabled', action='store_true', help=(
            'Also list disabled (inactive) hosts'))
        parser.add_argument('groups', nargs='?', default='*', help=(
            'Which hostgroups to operate on, allows globbing'))
        parser.add_argument('hosts', nargs='?', default='*', help=(
            'Which hostconfigs to operate on, allows globbing'))

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        if options['output'] == 'text':
            func = self.output_text
        elif options['output'] == 'html':
            func = self.output_html
        elif options['output'] == 'email':
            func = self.output_email
        else:
            assert False, options

        hostconfigs = self.get_hostconfigs(
            options['groups'], options['hosts'])

        self.run_per_group(func, hostconfigs, options['force'])

    def get_hostconfigs(self, groups_glob, hosts_glob, with_disabled=False):
        groups = HostGroup.objects.all()
        hosts = HostConfig.objects.all()

        groups = [
            group for group in groups if fnmatch(group.name, groups_glob)]
        hosts = HostConfig.objects.filter(id__in=(
            host.id for host in (
                hosts.filter(hostgroup__in=groups)
                .prefetch_related('hostgroup'))
            if fnmatch(host.friendly_name, hosts_glob)))

        return hosts

    def run_per_group(self, func, qs, force_send):
        # Fix so we can aggregate by group below.
        qs = qs.order_by(
            'hostgroup__name', 'hostgroup__id', 'friendly_name', 'id')

        lastgroup = None
        hosts = []
        for host in qs:
            if lastgroup != host.hostgroup:
                if lastgroup is not None:
                    func(lastgroup, hosts, force_send)
                lastgroup = host.hostgroup
                hosts = []
            hosts.append(host)

        if lastgroup is not None:
            func(lastgroup, hosts, force_send)

    def generate_subject(self, hostgroup, hosts):
        hosts_disabled = sum(
            1 for i in hosts if not i.enabled)
        hosts_failed = sum(
            1 for i in hosts if i.enabled and not i.last_backuprun.success)

        subject = _('%s backup report "%s"') % (
            settings.COMPANY_NAME, hostgroup.name)
        if hosts_disabled or hosts_failed:
            subject += _(' (%d failed, %d disabled)') % (
                hosts_failed, hosts_disabled)

        return subject

    def generate_text(self, hostgroup, hosts):
        context = {
            'hostgroup': hostgroup,
            'hosts': hosts,
            'company_name': settings.COMPANY_NAME,
            'company_email': settings.COMPANY_EMAIL,
        }

        return render_to_string('planb/report_email_body.txt', context)

    def generate_html(self, text):
        try:
            # Run rst2html binary and hope that it exists.
            html = check_output(['rst2html'], input=text.encode('utf-8'))
        except OSError:
            html = None
        else:
            html = html.decode('utf-8')

        return html

    def output_text(self, hostgroup, hosts, force_send):
        text = self.generate_text(hostgroup, hosts)
        self.stdout.write(text)

    def output_html(self, hostgroup, hosts, force_send):
        text = self.generate_text(hostgroup, hosts)
        html = self.generate_html(text)
        self.stdout.write(html)

    def output_email(self, hostgroup, hosts, force_send):
        if not hostgroup.notify_email:
            self.stderr.write(
                'No notify addresses for group {}, skipping..'.format(
                    hostgroup))
            return

        if not force_send and hostgroup.last_monthly_report:
            a_while_ago = timezone.now() - timedelta(days=25)
            if hostgroup.last_monthly_report > a_while_ago:
                self.stderr.write(
                    'Already sent to group {} recently, skipping..'.format(
                        hostgroup))
                return

        subject = self.generate_subject(hostgroup, hosts)
        text = self.generate_text(hostgroup, hosts)
        html = self.generate_html(text)

        recipients = hostgroup.notify_email
        assert recipients and all('@' in i for i in recipients), recipients
        self.stdout.write(
            'Sending report for {} to {}'.format(hostgroup, recipients))
        self.send_mail(
            subject=subject, text_message=text, html_message=html,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            bcc_list=[settings.COMPANY_EMAIL])

        hostgroup.last_monthly_report = timezone.now()
        hostgroup.save(update_fields=['last_monthly_report'])

    def send_mail(self, subject, text_message, html_message, from_email,
                  recipient_list, bcc_list):
        connection = get_connection(
            username=None, password=None, fail_silently=False)

        mail = EmailMultiAlternatives(
            subject, text_message, from_email, recipient_list,
            bcc=bcc_list, connection=connection)
        if html_message:
            mail.attach_alternative(html_message, 'text/html')

        return mail.send()
