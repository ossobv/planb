import json

from django.core.exceptions import ObjectDoesNotExist

from planb.models import Fileset
from planb.management.base import BaseCommandWithZabbix


class Command(BaseCommandWithZabbix):
    help = 'Lists filesets'

    def handle(self, *args, **options):
        qs = (
            Fileset.objects.filter(is_enabled=True)
            .prefetch_related('hostgroup')
            .order_by('hostgroup__name', 'friendly_name'))
        if options['zabbix']:
            self.dump_zabbix_discovery(qs)
        else:
            self.dump_list(qs)

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

    def dump_zabbix_discovery(self, qs):
        data = [{'{#BKNAME}': fileset.unique_name} for fileset in qs]
        self.stdout.write(json.dumps({'data': data}) + '\n')
