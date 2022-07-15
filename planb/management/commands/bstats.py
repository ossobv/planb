from datetime import timedelta
from fnmatch import fnmatch

from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, When

from django.utils import timezone

from planb.models import Fileset, HostGroup


class Command(BaseCommand):
    help = 'Show year stats'

    def add_arguments(self, parser):
        parser.add_argument('groups', nargs='?', default='*', help=(
            'Which hostgroups to operate on, allows globbing'))
        parser.add_argument('filesets', nargs='?', default='*', help=(
            'Which filesets to operate on, allows globbing'))

        return super().add_arguments(parser)

    def handle(self, *args, **options):
        func = self.output_text

        filesets = self.get_filesets(
            options['groups'], options['filesets'])

        self.run_per_group(func, filesets)

    def get_filesets(self, groups_glob, filesets_glob, with_disabled=True):
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

    def run_per_group(self, func, qs):
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
                    func(lastgroup, filesets)
                lastgroup = fileset.hostgroup
                filesets = []
            filesets.append(fileset)

        if lastgroup is not None:
            func(lastgroup, filesets)

    def output_text(self, group, filesets):
        printed_header = False
        for fs in filesets:
            failures = self.process_fileset(fs)
            failures = [i for i in failures if i.duration_hours >= 48]
            if failures:
                if not printed_header:
                    print(
                        group,
                        ':: Backups failed longer than 48h in the past year:')
                    printed_header = True
                print(' ', fs)
                for fail in failures:
                    print(
                        '  - %s - failed for %.1f hours (%d failed attempts)'
                        % (fail.first.strftime('%Y-%m-%d %H:%M'),
                           fail.duration_hours, fail.count))
        if printed_header:
            print()

    def process_fileset(self, fs):
        class Failure:
            def __init__(self, first, last, count):
                self.first = first
                self.last = last
                self.duration_hours = (last - first).total_seconds() / 3600
                self.count = count

        one_year_ago = timezone.now() - timedelta(days=366)

        failures = []
        failed_first = None
        for pk, is_success, started in (
                fs.backuprun_set.filter(started__gte=one_year_ago)
                .values_list('pk', 'success', 'started')
                .order_by('started')):
            if failed_first is None:
                if is_success:
                    pass
                else:
                    failed_first = started
                    failed_attempts = 1
            else:
                if is_success:
                    failed_last = started
                    failures.append(
                        Failure(failed_first, failed_last, failed_attempts))
                    failed_first = None
                else:
                    failed_attempts += 1

        # NOTE: not storing "still failing" runs now..
        if failed_first is not None:
            assert not fs.is_enabled, (fs, 'failing and still enabled')

        return failures
