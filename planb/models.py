from datetime import datetime
import logging

from django.apps import apps
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.core.mail import mail_admins
from django.core.validators import RegexValidator
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _, ngettext, gettext_noop

from django_q.brokers.redis_broker import Redis

from planb.common.fields import MultiEmailField
from planb.signals import backup_done
from planb.storage import storage_pools
from planb.storage.base import DatasetNotFound, datetime_from_snapshot_name
from planb.utils import RETENTION_PERIOD_ADVANCED


logger = logging.getLogger(__name__)

BOGODATE = datetime(1970, 1, 2, tzinfo=timezone.utc)

validate_retention = RegexValidator(
    r'^(\d+[ymwdh],?)*$', message=_('Enter a valid value like 6m,4w,7d'))
validate_blacklist_hours = RegexValidator(
    r'^((\d+(?:-\d+)?,?)*|none)$', message=_(
        'Enter a valid value like 2,9-17 or none to disable blacklist hours'))


class _DecoratedSnapshot:
    "Snapshot name decorated with date and diff"
    @classmethod
    def iterator(cls, sorted_snapshots):
        "Decorating snapshot iterator"
        if sorted_snapshots:
            prev = next_ = cls(sorted_snapshots[0])
            for next_ in sorted_snapshots[1:]:
                next_ = cls(next_, prev)
                yield prev
                prev = next_
            yield next_

    def __init__(self, name, prev=None):
        self.name = name
        self.prev = prev
        if prev:
            self.prev.next = self
        self.next = None  # until the next one sets it
        try:
            self.date = datetime_from_snapshot_name(name)
        except ValueError:
            self.date = None

    def diff(self):
        "Diff between this and older snapshot"
        if self.date and self.prev and self.prev.date:
            diff = self.date - self.prev.date
            secs = diff.total_seconds()
            return '+{}'.format(self._human_time(secs))
        return ''

    def rdiff(self):
        "Diff between this and newer snapshot"
        if self.date and self.next and self.next.date:
            diff = self.next.date - self.date
            secs = diff.total_seconds()
            return '-{}'.format(self._human_time(secs))
        return ''

    def _human_time(self, secs):
        if secs < 300:      # <5m = 300s ('m' is reserved for month)
            return '{:.0f} second'.format(secs)
        if secs < 18000:    # <5h
            return '{:.0f} hour'.format((secs + 1800) // 3600)
        if secs < 432000:   # <5d
            return '{:.0f} day'.format((secs + 43200) // 86400)
        if secs < 1728000:  # <20d
            return '{:.0f} week'.format((secs + 302400) // 604800)
        month = 2629800     # 365.25 * 86400 / 12
        return '{:.0f} month'.format((secs + month // 2) // month)

    def __str__(self):
        return self.name


class _StrIntSorted:
    """
    Quick and dirty sortable string
    """
    def __init__(self, s, val):
        self.s = s
        self.val = val

    def __eq__(self, other):
        if isinstance(other, _StrIntSorted):
            return self.val == other.val
        return self.val == other

    def __lt__(self, other):
        if isinstance(other, _StrIntSorted):
            return self.val < other.val
        return self.val < other

    def __repr__(self):
        return '_StrIntSorted({!r}, {})'.format(self.s, self.val)

    def __str__(self):
        return self.s


class HostGroup(models.Model):
    name = models.CharField(max_length=63, unique=True)
    notify_email = MultiEmailField(
        blank=True, null=True,
        help_text=_('Use a newline per emailaddress'))
    last_monthly_report = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, help_text=_(
        'Description, guidelines and agreements for the hostgroup.'))

    blacklist_hours = models.CharField(
        _('Blacklist hours'), max_length=31, blank=True,
        validators=[validate_blacklist_hours], help_text=_(
            'Specify hours during which backups are disabled using notation '
            'h,h-h or none to disable blacklist hours. When left empty the '
            'system blacklist hours are used.'))
    retention = models.CharField(
        max_length=31, blank=True, validators=[validate_retention],
        help_text=_(
            'The backup retention period using notation <n><period> separated '
            'by comma: 1y,6m,3w,15d. When left empty the system retention '
            'periods are used.'))

    def get_blacklist_hours(self):
        if self.blacklist_hours:
            return self.blacklist_hours
        return settings.PLANB_BLACKLIST_HOURS
    get_blacklist_hours.short_description = _('Blacklist hours')

    def get_retention(self):
        if self.retention:
            return self.retention
        return settings.PLANB_RETENTION
    get_retention.short_description = _('Retention')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name',)


class Tag(models.Model):
    name = models.CharField(max_length=63, unique=True)
    description = models.TextField()

    def __str__(self):
        return self.name.lower()


class FilesetLock(object):
    def __init__(self, fileset_id, timeout=86400):
        self._fileset_id = fileset_id
        self._is_acquired = False
        self.timeout = timeout

    @cached_property
    def lock(self):
        return Redis.get_connection().lock(
            'fileset:{}'.format(self._fileset_id), sleep=1,
            timeout=self.timeout)

    def __enter__(self):
        # Use blocking so the contained code is only executed when the lock is
        # acquired.
        self.acquire(blocking=True)
        # Provide the current Fileset for the context.
        try:
            fileset = Fileset.objects.get(pk=self._fileset_id)
        except Exception:
            self.release()
            raise
        return fileset

    def __exit__(self, type, value, traceback):
        self.release()

    def is_acquired(self):
        return self._is_acquired

    def acquire(self, blocking=None):
        assert not self._is_acquired
        self._is_acquired = self.lock.acquire(blocking=blocking)
        return self._is_acquired

    def release(self):
        assert self._is_acquired
        self.lock.release()
        self._is_acquired = False


class Fileset(models.Model):
    friendly_name = models.CharField(
        verbose_name=_('Name'), max_length=63,
        help_text=_('Short name, should be unique per host group.'))
    hostgroup = models.ForeignKey(
        HostGroup, related_name='filesets', on_delete=models.PROTECT)
    notes = models.TextField(blank=True, help_text=_(
        'Quick description/tips. The first line is shown in the list view.'))
    tags = models.ManyToManyField(Tag, blank=True)

    # The storage alias is selected when adding the Fileset. Available choices
    # are selected from the storage pools in the FilesetForm.
    storage_alias = models.CharField(_('Storage'), max_length=31)
    dataset_name = models.CharField(
        verbose_name=_('Dataset name'), editable=False, max_length=254,
        help_text=_('The complete dataset name for the storage.'))

    last_ok = models.DateTimeField(
        _('Last backup success'), blank=True, null=True)
    last_run = models.DateTimeField(
        _('Last backup attempt'), default=BOGODATE)
    first_fail = models.DateTimeField(
        _('First backup failure'), blank=True, null=True)

    total_size_mb = models.PositiveIntegerField(
        default=0, db_index=True,
        help_text=_('Estimated total backup size in MiB.'))
    average_duration = models.PositiveIntegerField(
        'Time', default=0,  # this value may vary..
        help_text=_('Average duration of successful jobs in seconds.'))

    do_snapshot_size_listing = models.BooleanField(
        _('Create disk usage summary'), blank=True, default=True,
        help_text=_(
            'Summarize disk usage after the transport. '
            'This can be slow if there are many files.'))

    is_enabled = models.BooleanField(default=True)
    is_running = models.BooleanField(default=False)
    is_queued = models.BooleanField(default=False)

    blacklist_hours = models.CharField(
        _('Blacklist hours'), max_length=31, blank=True,
        validators=[validate_blacklist_hours], help_text=_(
            'Specify hours during which backups are disabled using notation '
            'h,h-h or none to disable blacklist hours. When left empty the '
            'hostgroup blacklist hours are used.'))
    retention = models.CharField(
        max_length=31, blank=True, validators=[validate_retention],
        help_text=_(
            'The backup retention period using notation <n><period> separated '
            'by comma: 1y,6m,3w,15d. When left empty the hostgroup retention '
            'periods are used.'))

    def __str__(self):
        return '{} ({})'.format(self.friendly_name, self.id)

    @cached_property
    def use_double_backup(self):
        return self.tags.filter(name='double-backup').exists()

    @property
    def unique_name(self):
        return '{}-{}'.format(self.hostgroup.name, self.friendly_name)

    @staticmethod
    def with_lock(fileset_id):
        return FilesetLock(fileset_id)

    def get_transport(self):
        ret = []
        for transport_class_name in settings.PLANB_TRANSPORTS:
            transport_class = apps.get_model(transport_class_name)
            ret.extend(transport_class.objects.filter(fileset=self))
        if not ret:
            raise ObjectDoesNotExist(
                'no transport for {!r}'.format(self))
        if len(ret) > 1:
            raise MultipleObjectsReturned(
                    'multiple transports for {!r}'.format(self))
        return ret[0]

    @cached_property
    def storage(self):
        return storage_pools[self.storage_alias]

    def get_blacklist_hours(self):
        return (self.blacklist_hours
                or self.hostgroup.blacklist_hours
                or settings.PLANB_BLACKLIST_HOURS)
    get_blacklist_hours.short_description = _('Blacklist hours')

    @property
    def is_in_blacklist_hours(self):
        hours = self.get_blacklist_hours()
        if hours != 'none':
            now = timezone.now()  # XXX should use fileset hosts localtime?
            for hour in hours.split(','):
                if '-' in hour:
                    start, end = map(int, hour.split('-'))
                    if start <= now.hour < end:
                        return True
                elif now.hour == int(hour):
                    return True
        return False

    def get_retention(self):
        for retention in (self.retention, self.hostgroup.retention):
            if retention:
                return retention
        return settings.PLANB_RETENTION
    get_retention.short_description = _('Retention')

    @property
    def retention_map(self):
        if not hasattr(self, '_retention_map'):
            self._retention_map = dict(
                (i[-1], int(i[:-1]))
                for i in self.get_retention().split(',')
                if i
            )
        return self._retention_map

    @property
    def hourly_retention(self):
        return self.retention_map.get('h', 0)

    @property
    def daily_retention(self):
        return self.retention_map.get('d', 0)

    @property
    def weekly_retention(self):
        return self.retention_map.get('w', 0)

    @property
    def monthly_retention(self):
        return self.retention_map.get('m', 0)

    @property
    def yearly_retention(self):
        return self.retention_map.get('y', 0)

    @property
    def retention_display(self):
        name_map = {
            'h': (gettext_noop('%(n)d hour'), gettext_noop('%(n)d hours')),
            'd': (gettext_noop('%(n)d day'), gettext_noop('%(n)d days')),
            'w': (gettext_noop('%(n)d week'), gettext_noop('%(n)d weeks')),
            'm': (gettext_noop('%(n)d month'), gettext_noop('%(n)d months')),
            'y': (gettext_noop('%(n)d year'), gettext_noop('%(n)d years')),
        }
        order = 'hdwmy'
        return ', '.join(
            ngettext(*name_map[period], self.retention_map[period]) % {
                'n': self.retention_map[period]}
            for period in sorted(self.retention_map, key=order.index)
            if self.retention_map[period] > 0
        )

    @property
    def total_size(self):
        return self.total_size_mb << 20

    @property
    def snapshot_size(self):
        try:
            return self.last_successful_backuprun.snapshot_size
        except BackupRun.DoesNotExist:
            return 0

    @cached_property
    def snapshot_count(self):
        return len(self.snapshot_list())

    @cached_property
    def snapshot_efficiency(self):
        "Return efficiency between 0% (poor) and 99% (efficient)"
        try:
            worst_case = self.total_size / self.snapshot_count
            efficiency = (100 * (self.snapshot_size - worst_case)
                          / (self.total_size - worst_case))
            efficiency = int(max(0, min(99, efficiency)))
            return _StrIntSorted('{:d}%'.format(efficiency), efficiency)
        except (ValueError, ZeroDivisionError):
            return _StrIntSorted('N/A', 101)  # sort above 100% efficiency

    @cached_property
    def last_backuprun(self):
        # If the backuprun has no duration it is still running.
        # The attributes on the Fileset still reflect that of the last
        # finished backuprun (success or failure) so we need to return that.
        return self.backuprun_set.exclude(duration=None).latest('started')

    @cached_property
    def last_successful_backuprun(self):
        return self.backuprun_set.filter(success=True).latest('started')

    def get_dataset(self):
        if not hasattr(self, '_get_dataset'):
            self._get_dataset = self.storage.get_dataset(self.dataset_name)
        return self._get_dataset

    def rename_dataset(self, new_dataset_name):
        self.get_dataset().rename_dataset(new_dataset_name)
        self.__class__.objects.filter(pk=self.pk).update(
            dataset_name=new_dataset_name)

        self.dataset_name = new_dataset_name
        if hasattr(self, '_get_dataset'):
            del self._get_dataset

    def clone(self, **override):
        # See: https://github.com/django/django/commit/a97ecfdea8
        copy = self.__class__.objects.get(pk=self.pk)
        copy.pk = None
        copy.last_ok = None
        copy.last_run = BOGODATE
        copy.first_fail = None
        copy.is_queued = copy.is_running = False
        copy.average_duration = 0
        copy.total_size_mb = 0
        copy.dataset_name = ''

        transport_overrides = {}
        # Use the overrides.
        for key, value in override.items():
            if key.startswith('transport__'):
                transport_overrides[key.replace('transport__', '')] = value
            else:
                setattr(copy, key, value)
        copy.save()

        try:
            transport = self.get_transport()
        except ObjectDoesNotExist:
            pass
        else:
            transport.clone(fileset=copy, **transport_overrides)

        return copy

    def should_backup(self):
        if not self.is_enabled:
            return False

        if self._has_recent_backup():
            return False

        self.refresh_from_db(fields=['is_running'])
        if self.is_running:
            return False

        return True

    def _has_recent_backup(self):
        # If the last backup failed, it is not recent.
        if self.first_fail is not None:
            return False

        # If there is no backup, it is not recent.
        if self.last_ok is None:
            return False

        order = 'hdwmy'
        for period in sorted(self.retention_map, key=order.index):
            if self.retention_map[period] > 0:
                period_has_advanced = RETENTION_PERIOD_ADVANCED[period]
                break
        else:
            logger.warning(
                '[%s] Backup disabled by retention policy: %s',
                self, self.retention)
            return True

        now = timezone.now()
        # Advances in the period value should trigger a backup.
        # This will cause backups to start at a similar time every interval.
        # e.g. dailies every day, hourlies every hour.
        if period_has_advanced(self.last_ok, now):
            return False

        return True

    def snapshot_rotate(self):
        dataset = self.get_dataset()
        if dataset.has_child_datasets():
            return dataset.child_dataset_snapshot_rotate(self.retention_map)
        return dataset.snapshot_rotate(self.retention_map)

    def snapshot_list(self):
        return self.get_dataset().snapshot_list()

    def snapshot_list_display(self):
        try:
            dataset = self.get_dataset()
        except DatasetNotFound:
            return ['(dataset not found in storage {!r})'.format(
                self.storage_alias)]
        snapshots = sorted(
            dataset.child_dataset_snapshot_list()
            if dataset.has_child_datasets()
            else dataset.snapshot_list())
        return _DecoratedSnapshot.iterator(snapshots)

    @property
    def has_child_datasets(self):
        return self.get_dataset().has_child_datasets()

    def get_next_snapshot_name(self):
        if not hasattr(self, '_next_snapshot_name'):
            snapname = datetime.utcnow().strftime('%Y%m%dT%H%MZ')  # yuck
            if settings.PLANB_PREFIX:
                snapname = '{}-{}'.format(settings.PLANB_PREFIX, snapname)
            # XXX: yuck
            self._next_snapshot_name = snapname
        return self._next_snapshot_name

    def snapshot_create(self, custom_prefix=None):
        snapname = datetime.utcnow().strftime('%Y%m%dT%H%MZ')  # XXX: see yuck

        prefix = custom_prefix or settings.PLANB_PREFIX
        if prefix:
            snapname = '{}-{}'.format(prefix, snapname)

        self.get_dataset().snapshot_create(snapname)
        logger.info('[%s] Created snapshot %s', self, snapname)
        return snapname

    def signal_done(self, success):
        instance = Fileset.objects.get(pk=self.pk)
        # Using send_robust, because we do not want user-code to mess up
        # the rest of our state.
        backup_done.send_robust(
            sender=self.__class__, fileset=instance, success=success)

    def save(self, *args, **kwargs):
        # Notify the same users who get ERROR / Success for backups that
        # the job was disabled/re-enabled.
        if self.pk:
            old_enabled = Fileset.objects.values_list(
                'is_enabled', flat=True).get(pk=self.pk)
            if self.is_enabled != old_enabled:
                mail_admins(
                    'INFO: Backup {} of {}'.format(
                        'ENABLED' if self.is_enabled else 'DISABLED', self),
                    'Toggled is_enabled-flag on {}.\n'.format(self))

        if not self.dataset_name:
            self.dataset_name = self.storage.name_dataset(
                self.hostgroup.name, self.friendly_name)
        return super().save(*args, **kwargs)

    class Meta:
        unique_together = (
            ('hostgroup', 'friendly_name'),
            ('storage_alias', 'dataset_name'),
        )


class BackupRun(models.Model):
    """
    Info about a single backup run. Some of these fields are duplicated
    in the Fileset model. We like those there too, so we use it to
    quickly sort those records.

    Runs with success==True show sensible info. For others you may need
    to take (some of) the values with a grain of salt.
    """
    fileset = models.ForeignKey(Fileset, on_delete=models.CASCADE)

    attributes = models.TextField(
        blank=True,
        help_text=_('YAML-safe dictionary of backup run attributes.'))
    started = models.DateTimeField(
        auto_now_add=True, db_index=True,
        help_text=_('When the backup run started.'))
    duration = models.PositiveIntegerField(
        blank=True, null=True,
        help_text=_('How long this backup run took in seconds.'))

    success = models.BooleanField(
        default=False, blank=True,
        help_text=_('If the backup succeeded, the other values can be '
                    'trusted.'))
    error_text = models.TextField(
        blank=True,
        help_text=_('Error messages; non-empty only if success is False.'))

    total_size_mb = models.PositiveIntegerField(
        default=0,
        help_text=_('Estimated total backup size in MiB.'))
    snapshot_size_mb = models.PositiveIntegerField(
        default=0,
        help_text=_('Estimated single backup size in MiB.'))
    snapshot_size_listing = models.TextField(
        blank=True,
        # This will be populated by dutree-output.
        help_text=_('YAML-safe "PATH: SIZE<LF>"{n} dictionary of paths.'))
    snapshot_name = models.CharField(
        max_length=30, blank=True,
        help_text=_('Custom snapshot name; will not be auto-deleted if set'))

    @property
    def total_size(self):
        return self.total_size_mb << 20

    @property
    def snapshot_size(self):
        return self.snapshot_size_mb << 20

    def snapshot_size_listing_as_list(self):
        if not self.snapshot_size_listing:
            return []

        list_ = []
        for line in self.snapshot_size_listing.splitlines():
            try:
                path, size = line.rsplit(':', 1)
                if path[0] == path[-1] == '"':
                    path = path[1:-1]
                size = int(size.replace(',', ''))
                list_.append((path, size))
            except Exception as e:
                raise ValueError(
                    'Parse error in snapshot_size_listing line {!r} '
                    'in backuprun {} for fileset {}'.format(
                        line, self, self.fileset_id)) from e
        return list_

    def __str__(self):
        return '<BackupRun({} #{}-{}{})>'.format(
            self.started.strftime('%Y-%m-%d'), self.fileset_id, self.pk,
            '' if self.success else ' failed')


@receiver(post_save, sender=Fileset)
def create_dataset(sender, instance, created, *args, **kwargs):
    if not instance.is_enabled:
        return

    dataset = instance.get_dataset()
    dataset.ensure_exists()
