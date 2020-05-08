from collections import OrderedDict
from fnmatch import fnmatch
import json

from django.core.exceptions import ObjectDoesNotExist

from planb.common.customyaml import CustomYaml
from planb.models import Fileset, HostGroup
from planb.management.base import BaseCommand


class HostAsConfigListingConfig(object):
    with_retention = True
    with_schedule = True


class HostAsConfig(object):
    def __init__(self, fileset, config=HostAsConfigListingConfig()):
        self._host = fileset
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
        # now all the options pretty much match the PlanB fileset data model
        # directly.
        return ('v', 1)
        # return ('v', ['list', '1', {'x': 1, 'y': 2}])  # test only

    def get_schedule(self):
        return ('schedule', 'nightly')

    def get_retention(self):
        return ('retention', OrderedDict((
            ('hourly', self._host.hourly_retention),
            ('daily', self._host.daily_retention),
            ('weekly', self._host.weekly_retention),
            ('monthly', self._host.monthly_retention),
            ('yearly', self._host.yearly_retention),
        )))

    def get_paths(self):
        return ('paths', OrderedDict((
            # Root of the filesystem. Should be '/'. In the future we
            # could put something else in here, like '/encrypted-root/'.
            self.get_root(),
            self.get_includes(),
            self.get_excludes(),
        )))

    def _split_paths(self, list_):
        paths = []
        if list_:  # skip the "empty string"-list
            for path in list_.split(' '):
                comment = None

                if False:
                    from random import choice
                    comment = choice(
                        ['Because this directory is\nawesome for the win!\n',
                         "here's the p0rn"]
                        + [None] * 8)

                paths.append(
                    dict([(path, comment)]) if comment else path)

        return paths

    def get_root(self):
        try:
            return ('root', self._host.get_transport().src_dir)
        except ObjectDoesNotExist:
            return ('root', None)

    def get_includes(self):
        # A list of "glob" names to include; started from 'root'
        # without that slash.
        try:
            return ('include', self._split_paths(
                self._host.get_transport().includes))
        except ObjectDoesNotExist:
            # No transport configured? Then no includes..
            return ('include', 'NO TRANSPORT CONFIGURED')

    def get_excludes(self):
        # A list of "glob" names to exclude; started from 'root'
        # without that slash.
        try:
            return ('exclude', self._split_paths(
                self._host.get_transport().excludes))
        except ObjectDoesNotExist:
            # No transport configured? Then no excludes..
            return ('exclude', 'NO TRANSPORT CONFIGURED')

    def get_notes(self):
        return ('notes', self._host.notes)

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
        parser.add_argument(
            '--json', action='store_const', const='json', dest='output',
            help=('Output configuration as JSON'))
        parser.add_argument(
            '--yaml', action='store_const', const='yaml', dest='output',
            help=('Output configuration as YAML'))
        parser.add_argument('--with-disabled', action='store_true', help=(
            'Also list disabled (inactive) hosts'))
        parser.add_argument('groups', nargs='?', default='*', help=(
            'Which hostgroups to operate on, allows globbing'))
        parser.add_argument('hosts', nargs='?', default='*', help=(
            'Which filesets to operate on, allows globbing'))

        parser.set_defaults(json=True)

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        filesets = self.get_filesets(
            options['groups'], options['hosts'],
            with_disabled=options['with_disabled'])
        listingconfig = HostAsConfigListingConfig()

        if options['minimal']:
            listingconfig.with_retention = False
            listingconfig.with_schedule = False

        if options['output'] == 'yaml':
            self.hosts2yaml(filesets, listingconfig)
        else:
            self.hosts2json(filesets, listingconfig)

    def hosts2json(self, filesets, listingconfig):
        for fileset in filesets:
            jsonblob = HostAsConfig(fileset, listingconfig).to_json()
            self.stdout.write('/* {} */\n\n{}\n\n'.format(
                fileset.unique_name, jsonblob))

    def hosts2yaml(self, filesets, listingconfig):
        for fileset in filesets:
            yamlblob = HostAsConfig(fileset, listingconfig).to_yaml()
            self.stdout.write('---\n# {}\n\n{}\n\n'.format(
                fileset.unique_name, yamlblob))

    def get_filesets(self, groups_glob, hosts_glob, with_disabled=False):
        groups = HostGroup.objects.all()
        hosts = Fileset.objects.all()
        if not with_disabled:
            hosts = hosts.exclude(is_enabled=False)

        groups = [
            group for group in groups if fnmatch(group.name, groups_glob)]
        hosts = [
            host for host in (
                hosts.filter(hostgroup__in=groups)
                .prefetch_related('hostgroup'))
            if fnmatch(host.friendly_name, hosts_glob)]
        return hosts
