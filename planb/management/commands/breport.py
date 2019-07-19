from datetime import timedelta
from fnmatch import fnmatch
from operator import attrgetter
from subprocess import check_output

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.mail import get_connection
from django.core.mail.message import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.template.defaultfilters import filesizeformat

from django.utils import timezone
from django.utils.translation import ugettext as _

from planb.core.models import Fileset, HostGroup


class Command(BaseCommand):
    help = 'Create backup report and optionally e-mail'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', choices=('text', 'html', 'email'), default='text',
            help='What kind of output; option email sends the mail')
        parser.add_argument('--force', action='store_true', help=(
            'If output is email and it was sent recently, send anyway'))
        parser.add_argument('--with-disabled', action='store_true', help=(
            'Also list disabled (inactive) filesets'))
        parser.add_argument('groups', nargs='?', default='*', help=(
            'Which hostgroups to operate on, allows globbing'))
        parser.add_argument('filesets', nargs='?', default='*', help=(
            'Which filesets to operate on, allows globbing'))

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

        filesets = self.get_filesets(
            options['groups'], options['filesets'], options['with_disabled'])

        self.run_per_group(func, filesets, options['force'])

    def get_filesets(self, groups_glob, filesets_glob, with_disabled=False):
        groups = HostGroup.objects.all()
        filesets = Fileset.objects.all()
        if not with_disabled:
            filesets = filesets.filter(is_enabled=True)

        groups = [
            group for group in groups if fnmatch(group.name, groups_glob)]
        filesets = Fileset.objects.filter(id__in=(
            fs.id for fs in filesets.filter(hostgroup__in=groups)
            if fnmatch(fs.friendly_name, filesets_glob)))

        return filesets.prefetch_related('hostgroup')

    def run_per_group(self, func, qs, force_send):
        # Fix so we can aggregate by group below.
        qs = qs.order_by(
            'hostgroup__name', 'hostgroup__id', 'friendly_name', 'id')

        lastgroup = None
        filesets = []
        for fileset in qs:
            if lastgroup != fileset.hostgroup:  # prefetched
                if lastgroup is not None:
                    func(lastgroup, filesets, force_send)
                lastgroup = fileset.hostgroup
                filesets = []
            filesets.append(fileset)

        if lastgroup is not None:
            func(lastgroup, filesets, force_send)

    def generate_subject(self, hostgroup, filesets):
        hosts_disabled = sum(
            1 for i in filesets if not i.is_enabled)
        hosts_failed = sum(
            1 for i in filesets
            if i.is_enabled and not i.last_backuprun.success)

        subject = _('%s backup report "%s"') % (
            settings.COMPANY_NAME, hostgroup.name)
        if hosts_disabled or hosts_failed:
            subject += _(' (%d failed, %d disabled)') % (
                hosts_failed, hosts_disabled)

        return subject

    def generate_text(self, hostgroup, filesets):
        # Add a display size property for the summary that includes the
        # fileset rank when ordered by total size.
        for i, fileset in enumerate(
                sorted(filesets, key=attrgetter('total_size'),
                       reverse=True), 1):
            if i < 4:
                # Add a rank to the first 3 filesets.
                if i in (2, 3):
                    rank = chr(176 + i)
                else:
                    rank = chr(8304 + i)
            else:
                rank = ' '
            fileset.total_size_display = '{}{}'.format(
                filesizeformat(fileset.total_size), rank)

        context = {
            'hostgroup': hostgroup,
            'filesets': filesets,
            'company_name': settings.COMPANY_NAME,
            'company_email': settings.COMPANY_EMAIL,
            'total_size': sum(i.total_size for i in filesets),
        }

        return render_to_string('planb/report_email_body.txt', context)

    def generate_html(self, text):
        cmd = ['rst2html']
        report_css = finders.find('planb/css/report.css')
        if report_css:
            # Embed our report.css after the default html4css1.css
            cmd.append(
                '--stylesheet-path=html4css1.css,{}'.format(report_css))
        try:
            # Run rst2html binary and hope that it exists.
            html = check_output(cmd, input=text.encode('utf-8'))
        except OSError:
            html = None
        else:
            html = html.decode('utf-8')

        return html

    def output_text(self, hostgroup, filesets, force_send):
        text = self.generate_text(hostgroup, filesets)
        self.stdout.write(text)

    def output_html(self, hostgroup, filesets, force_send):
        text = self.generate_text(hostgroup, filesets)
        html = self.generate_html(text)
        self.stdout.write(html)

    def output_email(self, hostgroup, filesets, force_send):
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

        subject = self.generate_subject(hostgroup, filesets)
        text = self.generate_text(hostgroup, filesets)
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
            mail.attach('pretty_report.html', html_message, 'text/html')

        return mail.send()
