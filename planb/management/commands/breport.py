from dateutil.relativedelta import relativedelta
from subprocess import check_output

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.message import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import render_to_string

from django.utils import timezone
from django.utils.translation import ugettext as _

from planb.models import HostConfig


class Command(BaseCommand):
    help = 'Email backup report'

    def handle(self, *args, **options):
        qs = (
            HostConfig.objects
            .filter(hostgroup__notify_email__contains='@')
            .select_related('hostgroup')
            .order_by('hostgroup__name', 'friendly_name'))
        self.send_monthly_reports(qs)

    def send_monthly_reports(self, qs):
        last_month = timezone.now() - relativedelta(days=25)
        qs = qs.filter(
            Q(hostgroup__last_monthly_report=None) |
            Q(hostgroup__last_monthly_report__lt=last_month))

        lastgroup = None
        hosts = []
        for host in qs:
            if lastgroup != host.hostgroup:
                if lastgroup is not None:
                    self.email_hostgroup(lastgroup, hosts)
                lastgroup = host.hostgroup
                hosts = []
            hosts.append(host)

        if lastgroup is not None:
            self.email_hostgroup(lastgroup, hosts)

    def email_hostgroup(self, hostgroup, hosts):
        hosts_disabled = sum(
            1 for i in hosts if not i.enabled)
        hosts_failed = sum(
            1 for i in hosts if i.enabled and not i.last_backuprun.success)

        context = {
            'hostgroup': hostgroup,
            'hosts': hosts,
            'company_name': settings.COMPANY_NAME,
            'company_email': settings.COMPANY_EMAIL,
        }
        subject = _('%s backup report "%s"') % (
            settings.COMPANY_NAME, hostgroup.name)
        if hosts_disabled or hosts_failed:
            subject += _(' (%d failed, %d disabled)') % (
                hosts_failed, hosts_disabled)

        message = render_to_string('planb/report_email_body.txt', context)
        try:
            html_message = check_output(['rst2html'], input=(
                message.encode('utf-8')))
        except OSError:
            html_message = None
        else:
            html_message = html_message.decode('utf-8')

        for recipient in (hostgroup.notify_email or [settings.COMPANY_EMAIL]):
            recipient = recipient.strip()
            if not recipient:
                continue
            self.stdout.write(
                'Sending report for {} to {}'.format(hostgroup, recipient))
            self.send_mail(
                subject=subject,
                text_message=message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
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
