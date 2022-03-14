from datetime import timedelta
from fnmatch import fnmatch
from operator import attrgetter
from subprocess import check_output

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.mail import get_connection
from django.core.mail.message import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, When
from django.template.loader import render_to_string
from django.template.defaultfilters import filesizeformat

from django.utils import timezone
from django.utils.translation import gettext as _

from planb.models import Fileset, HostGroup
from planb.templatetags.planb import column_width


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
            'hostgroup__name', 'hostgroup__id',
            '-is_enabled', Case(
                When(first_fail=None, then=1),
                default=0, output=IntegerField()),
            'friendly_name', 'id')

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

    @staticmethod
    def _get_nth_place_icon(pos):
        """
        Return icon-string; note that we need to count the character width

        The emojis returned include wide characters, which take up two columns
        in a monospaced font.

        Because:

            emoji characters were first developed through the use of extensions
            of legacy East Asian encodings, such as Shift-JIS, and in such a
            context they were treated as wide characters. While these
            extensions have been added to Unicode or mapped to standardized
            variation sequences, their treatment as wide characters has been
            retained, and extended for consistency with emoji characters that
            lack a legacy encoding.

        That means that:

            import unicodedata

            count = (lambda s: sum(
                2 if unicodedata.east_asian_width(ch) == 'W' else 1
                for ch in s))

            3 == count('321')
            3 == count('32\u00b9')
            4 == count('32\U0001F947')

        See the unicode_rjust filter for a possible implementation.
        """
        if pos == 1:
            # return '\u00b9'           # U+00B9 SUPERSCRIPT ONE
            # return '\U0001F3C6'       # :trophy:
            return '\U0001F947'         # :1st_place_medal:

        if pos == 2:
            # return chr(0x00b0 + pos)  # U+00B2
            return '\U0001F948'         # :2nd_place_medal:

        if pos == 3:
            # return chr(0x00b0 + pos)  # U+00B3
            return '\U0001F949'         # :3rd_place_medal

        if pos >= 4:
            return '  '  # two chars

        assert False, ('we do not get here', pos)
        return chr(0x2070 + pos)        # U+2070, U+2074..U+2079

    def _make_friendly_name(self, o, rjust):
        flags = []
        if not o.is_enabled:
            # flags.append('\u23fb')        # Power Symbol (single width)
            # flags.append('\u274C')        # :cross_mark:
            # flags.append('\U0001F6AB')    # :prohibited:
            # flags.append('\u23f9\ufe0f')  # :stop_button: (too wide)
            flags.append('\U0001F6D1')      # :stop_sign:
        elif o.first_fail:
            # flags.append('\u26a0')        # Warning Sign (single width)
            flags.append('\u26a0\ufe0f')    # :warning:
        if o.use_double_backup:
            # flags.append('\u2194\ufe0f')  # :left_right_arrow: (too wide)
            flags.append('\U0001F4A0')      # :diamond_with_a_dot:
        flags = ''.join(flags)

        name = o.friendly_name + flags
        namelen = column_width(name)
        if namelen < rjust:
            name = (o.friendly_name + (' ' * (rjust - namelen)) + flags)
        elif namelen > rjust:
            assert rjust > 10, 'we tack on 8 to the right'
            remove = namelen - rjust + 2
            name = (name[0:-(remove + 8)] + '..' + name[-8:])

        return name

    def generate_text(self, hostgroup, filesets):
        # Create readable filename and add properties. And adjust to fit into
        # the full column.
        for o in filesets:
            o.friendly_name_display = self._make_friendly_name(o, rjust=31)

        # Add a display size property for the summary that includes the
        # fileset rank when ordered by largest size.
        for r, o in enumerate(sorted(
                filesets, key=attrgetter('total_size'), reverse=True), 1):
            o.total_size_display = (
                filesizeformat(o.total_size) + self._get_nth_place_icon(r))

        # Add an efficiency display property for the summary that includes the
        # efficient rank when ordered by worst efficiency.
        for r, o in enumerate(sorted(
                filesets, key=attrgetter('snapshot_efficiency')), 1):
            o.snapshot_efficiency_display = (
                str(o.snapshot_efficiency) + self._get_nth_place_icon(r))

        context = {
            'hostgroup': hostgroup,
            'filesets': filesets,
            'company_name': settings.COMPANY_NAME,
            'company_email': settings.COMPANY_EMAIL,
            'total_size': sum(i.total_size for i in filesets),
        }

        try:
            rendered = render_to_string('planb/report_email_body.txt', context)
        except Exception as e:
            raise ValueError(
                'Render issue in hostgroup {} in one of filesets: {}'.format(
                    hostgroup, ', '.join(str(i.id) for i in filesets))) from e

        return rendered

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
