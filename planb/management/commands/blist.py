import json

from planb.management.base import BaseCommandWithZabbix
from planb.models import HostConfig


class Command(BaseCommandWithZabbix):
    help = 'Lists hosts'

    def handle(self, *args, **options):
        qs = (
            HostConfig.objects.filter(enabled=True)
            .order_by('hostgroup__name', 'friendly_name'))
        if options['zabbix']:
            self.dump_zabbix_discovery(qs)
        else:
            self.dump_list(qs)

    def dump_list(self, qs):
        ret = []

        lastgroup = None
        for host in qs:
            if lastgroup != host.hostgroup:
                lastgroup = host.hostgroup
                if ret:
                    ret.append('')
                ret.append('[{}]'.format(host.hostgroup))
            ret.append('{host.friendly_name:30s}  {host.host}'.format(
                host=host))

        if ret:
            ret.append('')

        self.stdout.write('\n'.join(ret) + '\n')

    def dump_zabbix_discovery(self, qs):
        data = [{'{#BKNAME}': host.identifier} for host in qs]
        self.stdout.write(json.dumps({'data': data}) + '\n')
