import json
import socket
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from planb.models import Fileset
from planb.management.base import BaseCommandWithZabbix


class Command(BaseCommandWithZabbix):
    help = 'Lists filesets'

    def add_arguments(self, parser):
        parser.add_argument('--summary', action='store_true', help=(
            'Show summary'))
        parser.add_argument('--double', action='store_true', help=(
            'Filesets with double-backup'))

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        qs = (
            Fileset.objects
            .prefetch_related('hostgroup')
            .order_by('hostgroup__name', 'friendly_name'))

        if options['summary']:
            assert options['zabbix']
            self.dump_zabbix_summary(qs)
        elif options['zabbix']:
            self.dump_zabbix_discovery(qs.filter(is_enabled=True))
        elif options['double']:
            # Include enabled and disabled hosts for the remote server.
            self.dump_dataset_list(qs.filter(tags__name='double-backup'))
        else:
            self.dump_list(qs.filter(is_enabled=True))

    def dump_list(self, qs):
        ret = []

        lastgroup = None
        for fileset in qs:
            if lastgroup != fileset.hostgroup:  # prefetched
                lastgroup = fileset.hostgroup
                if ret:
                    ret.append('')
                ret.append('[{}]'.format(fileset.hostgroup))

            try:
                transport = fileset.get_transport()
            except ObjectDoesNotExist:
                transport = 'MISSING_TRANSPORT'

            ret.append('{fileset.friendly_name:30s}  {transport}'.format(
                fileset=fileset, transport=transport))

        if ret:
            ret.append('')

        self.stdout.write('\n'.join(ret) + '\n')

    def dump_dataset_list(self, qs):
        for fileset in qs:
            self.stdout.write(fileset.dataset_name)

    def dump_zabbix_discovery(self, qs):
        hostname = socket.gethostname()
        data = [{
            '{#ID}': fileset.pk,
            '{#NAME}': fileset.unique_name,
            '{#PLANB}': hostname} for fileset in qs]
        self.stdout.write(json.dumps(data) + '\n')

    def dump_zabbix_summary(self, qs):
        hostname = socket.gethostname()
        disqs = qs.filter(is_enabled=False)
        enqs = qs.filter(is_enabled=True)
        now = timezone.now()

        latest_success, oldest_success, latest_failure, oldest_failure = (
            self._get_first_and_last_success_and_fail(qs, now))

        def as_age(tm):
            return int((now - tm).total_seconds())

        data = {
            'enabled': enqs.count(),
            # NOTE: Includes "manual" failure when forcing a backup...
            'failed': enqs.filter(first_fail__isnull=False).count(),
            'latest_success': as_age(latest_success),
            'oldest_success': as_age(oldest_success),
            'latest_failure': as_age(latest_failure),
            'oldest_failure': as_age(oldest_failure),
            'disabled': disqs.count(),
            'hostname': hostname}
        self.stdout.write(json.dumps(data) + '\n')

    def _get_first_and_last_success_and_fail(self, qs, now):
        t0 = datetime(1970, 1, 1, tzinfo=timezone.utc)
        oldest_success = oldest_failure = now
        latest_success = latest_failure = t0

        for fileset in qs.filter(is_enabled=True):
            failed_at = self._get_first_failure_if_failed(fileset, now)

            if failed_at:
                oldest_failure = min(oldest_failure, failed_at)
                latest_failure = max(latest_failure, failed_at)
            elif fileset.last_ok:
                oldest_success = min(oldest_success, fileset.last_ok)
                latest_success = max(latest_success, fileset.last_ok)
            else:
                # Neither failure nor success. New fileset?
                pass

        if latest_success == t0:
            latest_success = now
        if latest_failure == t0:
            latest_failure = now

        return latest_success, oldest_success, latest_failure, oldest_failure

    def _get_first_failure_if_failed(self, fileset, now):
        if not fileset.first_fail:
            # Not failed
            return None

        # Don't use the first_fail property, as that is set to a bogus
        # date when doing a manual backup. Instead check the list of
        # backupruns.
        fail_qs = fileset.backuprun_set.order_by('-started')
        try:
            failed_at = fail_qs[0].started
        except IndexError:
            failed_at = now
        else:
            for run in fail_qs:
                if run.success:
                    break
                failed_at = run.started

        return failed_at
