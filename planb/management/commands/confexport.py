from collections import OrderedDict
from fnmatch import fnmatch
import json
import re

from planb.management.base import BaseCommand
from planb.models import HostConfig, HostGroup


class CustomYaml(object):
    """
    Custom YAML dumper that fits the PlanB config export needs exactly.

    The regular YAML dumper would add lots of tags that we don't need.
    This one is just right for this particular output.

    The ugly backslash (\\b) hack signifies that we prefer the data to
    be on the previous line.
    """
    # No need for double quotes around these:
    _yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9/_.-]*$')

    def __init__(self, obj):
        self._parsed = self._to_string(obj)

    def __str__(self):
        return '\n'.join(self._parsed)

    def _to_string(self, obj):
        return self._from_dict(obj, root=True)

    def _from_obj(self, obj):
        if isinstance(obj, (dict, list, tuple)):
            if len(obj) == 0:
                if isinstance(obj, (dict,)):
                    return ['\b', '{}']
                else:
                    return ['\b', '[]']
            if isinstance(obj, dict):
                return self._from_dict(obj)
            return self._from_list(obj)

        # |<LF>preformatted string<LF>
        if isinstance(obj, str) and '\n' in obj:
            obj = obj.rstrip()  # no need for trailing LFs here
            return ['\b', '|'] + ['  {}'.format(i) for i in obj.split('\n')]

        return ['\b', self._from_atom(obj)]

    def _from_list(self, list_):
        ret = []
        for item in list_:
            if isinstance(item, (list, tuple)):
                raise NotImplementedError('list in list')
            subret = self._from_obj(item)
            if subret[0] == '\b':
                ret.append('- {}'.format(subret[1]))
                ret.extend(['  {}'.format(i) for i in subret[2:]])
            else:
                assert subret[0].startswith('  ')
                subret[0] = '- {}'.format(subret[0][2:])
                ret.extend(subret)

        return ['  {}'.format(i) for i in ret]

    def _from_dict(self, dict_, root=False):
        ret = []
        for key, value in dict_.items():
            subret = self._from_obj(value)
            if subret[0] == '\b':
                ret.append('{}: {}'.format(
                    self._from_atom(key), subret[1]))
                ret.extend(subret[2:])
            else:
                ret.append('{}:'.format(self._from_atom(key)))
                ret.extend(subret)

        if not root:
            return ['  {}'.format(i) for i in ret]

        return ret

    def _from_atom(self, atom):
        if isinstance(atom, str):
            return self._from_string(atom)
        if atom is None:
            return 'null'  # or '~'
        if atom is True:
            return 'true'
        if atom is False:
            return 'false'
        if isinstance(atom, (int, float)):
            return str(atom)
        return self._from_string(str(atom))

    def _from_string(self, string):
        assert isinstance(string, str), string
        if string.lower() in ('null', 'true', 'false'):
            return '"{}"'.format(string)
        if self._yaml_safe_re.match(string):
            return string
        if '\n' in string:
            raise NotImplementedError('did not expect LF here')
        return '"{}"'.format(
            str(string).replace('\\', '\\\\')
            .replace('"', '\\"'))


class HostAsConfigListingConfig(object):
    with_retention = True
    with_schedule = True


class HostAsConfig(object):
    def __init__(self, hostconfig, config=HostAsConfigListingConfig()):
        self._host = hostconfig
        self._config = config

    def get_all(self):
        ret = OrderedDict()
        # Skipping unneeded version: ret.update([self.get_version()])

        if self._config.with_retention:
            ret.update([self.get_retention()])
        if self._config.with_schedule:
            ret.update([self.get_schedule()])

        ret.update([
            self.get_paths(),
            self.get_notes(),
        ])

        return ret

    def get_version(self):
        # Optionally version info? Should we call this options instead? Right
        # now all the options pretty much match the PlanB hostconfig data model
        # directly.
        return ('v', 1)
        # return ('v', ['list', '1', {'x': 1, 'y': 2}])  # test only

    def get_schedule(self):
        return ('schedule', 'nightly')

    def get_retention(self):
        return ('retention', OrderedDict((
            ('daily', self._host.retention),
            ('weekly', self._host.weekly_retention),
            ('monthly', self._host.monthly_retention),
            ('yearly', self._host.yearly_retention),
        )))

    def get_paths(self):
        return ('paths', OrderedDict((
            # Root of the filesystem. Should be '/'. In the future we
            # could put something else in here, like '/encrypted-root/'.
            ('root', '/'),
            self.get_includes(),
            self.get_excludes(),
        )))

    def _split_paths(self, list_):
        from random import choice

        paths = []
        if list_:  # skip the "empty string"-list
            for path in list_.split(' '):
                comment = choice(
                    ['Because this directory is\nawesome for the win!\n',
                     "here's the p0rn"] +
                    [None] * 8)
                paths.append(
                    dict([(path, comment)]) if comment else path)

        return paths

    def get_includes(self):
        # A list of "glob" names to include; started from 'root'
        # without that slash.
        return ('include', self._split_paths(self._host.includes))

    def get_excludes(self):
        # A list of "glob" names to exclude; started from 'root'
        # without that slash.
        return ('exclude', self._split_paths(self._host.excludes))

    def get_notes(self):
        return ('notes', self._host.description)

    def to_dict(self):
        return self.get_all()

    def to_json(self):
        return json.dumps(self.to_dict(), indent='  ')

    def to_yaml(self):
        # return yaml.dump(self.to_dict())
        return str(CustomYaml(self.to_dict()))


class Command(BaseCommand):
    help = 'Export host configuration to JSON or YAML'

    def add_arguments(self, parser):
        parser.add_argument('--minimal', action='store_true', help=(
            'Do not show retention/schedule'))
        parser.add_argument('--yaml', action='store_true', help=(
            'Output configuration as YAML instead of JSON'))
        parser.add_argument('--with-disabled', action='store_true', help=(
            'Also list disabled (inactive) hosts'))
        parser.add_argument('groups', nargs='?', default='*', help=(
            'Which hostgroups to operate on, allows globbing'))
        parser.add_argument('hosts', nargs='?', default='*', help=(
            'Which hostconfigs to operate on, allows globbing'))

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        hostconfigs = self.get_hostconfigs(
            options['groups'], options['hosts'],
            with_disabled=options['with_disabled'])
        listingconfig = HostAsConfigListingConfig()

        if options['minimal']:
            listingconfig.with_retention = False
            listingconfig.with_schedule = False

        if options['yaml']:
            self.hosts2yaml(hostconfigs, listingconfig)
        else:
            self.hosts2json(hostconfigs, listingconfig)

    def hosts2json(self, hostconfigs, listingconfig):
        for hostconfig in hostconfigs:
            jsonblob = HostAsConfig(hostconfig, listingconfig).to_json()
            self.stdout.write('/* {} */\n\n{}\n\n'.format(
                hostconfig.identifier, jsonblob))

    def hosts2yaml(self, hostconfigs, listingconfig):
        for hostconfig in hostconfigs:
            yamlblob = HostAsConfig(hostconfig, listingconfig).to_yaml()
            self.stdout.write('---\n# {}\n\n{}\n\n'.format(
                hostconfig.identifier, yamlblob))

    def get_hostconfigs(self, groups_glob, hosts_glob, with_disabled=False):
        groups = HostGroup.objects.all()
        hosts = HostConfig.objects.all()
        if not with_disabled:
            hosts = hosts.exclude(enabled=False)

        groups = [
            group for group in groups if fnmatch(group.name, groups_glob)]
        hosts = [
            host for host in (
                hosts.filter(hostgroup__in=groups)
                .prefetch_related('hostgroup'))
            if fnmatch(host.friendly_name, hosts_glob)]
        return hosts
