import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.apps import apps
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.core.mail import mail_admins
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _, ngettext

from planb.common.fields import MultiEmailField
from planb.common.subprocess2 import (
    CalledProcessError, check_output)
from planb.signals import backup_done
from planb.storage.base import DatasetNotFound, Storage
from planb.storage.zfs import Zfs


logger = logging.getLogger(__name__)

BOGODATE = datetime(1970, 1, 2, tzinfo=timezone.utc)

bfs = Zfs(binary=settings.PLANB_ZFS_BIN, sudobin=settings.PLANB_SUDO_BIN)


def get_pools():
    pools = []
    for name, pool, bfs in settings.PLANB_STORAGE_POOLS:
        assert bfs == 'zfs', bfs
        try:
            val1 = float(check_output(
                [settings.PLANB_SUDO_BIN, settings.PLANB_ZFS_BIN,
                 'get', '-Hpo', 'value', 'used', pool]).strip())
            val2 = float(check_output(
                [settings.PLANB_SUDO_BIN, settings.PLANB_ZFS_BIN,
                 'get', '-Hpo', 'value', 'available', pool]).strip())
        except (CalledProcessError, ValueError):
            # If the ZFS CLI binary is not found, or if you use a bogus
            # binary (/bin/true) which returns no valid values, don't
            # die, but let the get_pools() return something sensible.
            available = pct = '???'
        else:
            pct = '{pct:.0f}%'.format(pct=(100 * (val1 / (val1 + val2))))
            available = int(val2 / 1024 / 1024 / 1024)

        pools.append((pool, '{}, {}G free ({} used)'.format(
            name, available, pct)))
    return tuple(pools)


class TransportChoices(models.PositiveSmallIntegerField):
    SSH = 0
    RSYNC = 1

    def __init__(self, *args, **kwargs):
        choices = (
            (self.SSH, _('ssh (default)')),
            (self.RSYNC, _('rsync (port 873)')),
        )
        super().__init__(default=self.SSH, choices=choices)


class HostGroup(models.Model):
    name = models.CharField(max_length=63, unique=True)
    notify_email = MultiEmailField(
        blank=True, null=True,
        help_text=_('Use a newline per emailaddress'))
    last_monthly_report = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ('name',)


class Fileset(models.Model):
    friendly_name = models.CharField(
        # FIXME: should be unique with hostgroup?
        verbose_name=_('Name'), max_length=63, unique=True,
        help_text=_('Short name, should be unique per host group.'))
    hostgroup = models.ForeignKey(
        HostGroup, related_name='filesets', on_delete=models.PROTECT)
    notes = models.TextField(blank=True, help_text=_(
        'Quick description/tips. Use the first line for labels/tags.'))

    dest_pool = models.CharField(max_length=254, choices=())  # set in forms.py

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
        help_text=_('Average duration of succesful jobs in seconds.'))

    do_snapshot_size_listing = models.BooleanField(
        _('Create disk usage summary'), blank=True, default=True,
        help_text=_(
            'Summarize disk usage after the transport. '
            'This can be slow if there are many files.'))

    is_enabled = models.BooleanField(default=True)
    is_running = models.BooleanField(default=False)
    is_queued = models.BooleanField(default=False)

    daily_retention = models.IntegerField(
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text=_('How many daily\'s do we keep?'))
    weekly_retention = models.IntegerField(
        default=3,
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
        help_text=_('How many weekly\'s do we keep?'))
    monthly_retention = models.IntegerField(
        default=11,
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
        help_text=_('How many monthly\'s do we keep?'))
    yearly_retention = models.IntegerField(
        default=1,
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
        help_text=_('How many yearly\'s do we keep?'))

    def __str__(self):
        return '{} ({})'.format(self.friendly_name, self.id)

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

    @property
    def basename(self):
        return '{}-{}'.format(self.hostgroup.name, self.friendly_name)

    @property
    def retention_display(self):
        retention = [
            ngettext(
                '%(days)dday', '%(days)ddays', self.daily_retention) % {
                'days': self.daily_retention}]
        if self.weekly_retention:
            retention.append(
                ngettext(
                    '%(weeks)dweek', '%(weeks)dweeks',
                    self.weekly_retention) % {
                    'weeks': self.weekly_retention})
        if self.monthly_retention:
            retention.append(
                ngettext(
                    '%(months)dmonth', '%(months)dmonths',
                    self.monthly_retention) % {
                    'months': self.monthly_retention})
        if self.yearly_retention:
            retention.append(
                ngettext(
                    '%(years)dyear', '%(years)dyears',
                    self.yearly_retention) % {
                    'years': self.yearly_retention})
        return ', '.join(retention)

    @property
    def total_size(self):
        return self.total_size_mb << 20

    @cached_property
    def last_backuprun(self):
        return self.backuprun_set.latest('started')

    @cached_property
    def last_successful_backuprun(self):
        return self.backuprun_set.filter(success=True).latest('started')

    def get_dataset(self):
        storage = Storage(bfs, self.dest_pool)
        return storage.get_dataset(str(self.hostgroup), self.friendly_name)

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

        # Use the overrides.
        for key, value in override.items():
            setattr(copy, key, value)

        copy.save()
        return copy

    def should_backup(self):
        if not self.is_enabled:
            return False

        if self._has_recent_backup():
            return False

        self.refresh_from_db()
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

        now = timezone.now()
        now_date_lo = timezone.localtime(now).date()
        backup_date_lo = timezone.localtime(self.last_ok).date()
        seconds_since_last = (now - self.last_ok).total_seconds()

        # If previous backup date is unequal to current date (both
        # localtime) and the last backup was more than 8 hours ago, it
        # is not recent.
        # This should make the backups start around 00:00 (localtime).
        if backup_date_lo < now_date_lo and (
                seconds_since_last >= (8 * 3600)):
            return False

        # If the last backup was "started" (using average duration) more
        # than 24 hours ago. If we decrease this, we can make the
        # backups start sooner than 00:00.
        if (seconds_since_last + self.average_duration) >= (24 * 3600):
            return False

        return True

    def snapshot_rotate(self):
        return bfs.snapshots_rotate(
            self.dest_pool, self.hostgroup,
            self.friendly_name,
            daily_retention=self.daily_retention,
            weekly_retention=self.weekly_retention,
            monthly_retention=self.monthly_retention,
            yearly_retention=self.yearly_retention)

    def snapshot_list(self):
        return bfs.snapshots_get(
            self.dest_pool, self.hostgroup, self.friendly_name)

    def snapshot_list_display(self):
        try:
            snapshots = self.snapshot_list()
        except DatasetNotFound:
            return ['(dataset not found in pool {!r})'.format(
                self.dest_pool)]
        return sorted([s.split('@')[-1] for s in snapshots])

    def snapshot_create(self):
        # Add logica what kind of snapshot
        # First we need to know what we have
        snapshots = bfs.snapshots_get(
            self.dest_pool, self.hostgroup, self.friendly_name)

        snaplist = []
        # FIXME: should use UTC dates here!
        if not snapshots:
            snaplist.append(datetime.now().strftime('daily-%Y%m%d%H%M'))
            if self.weekly_retention:
                snaplist.append(datetime.now().strftime('weekly-%Y%m%d%H%M'))
            if self.monthly_retention:
                snaplist.append(datetime.now().strftime('monthly-%Y%m%d%H%M'))
            if self.yearly_retention:
                snaplist.append(datetime.now().strftime('yearly-%Y%m%d%H%M'))
        else:
            # Do we need a daily? We do, otherwise we wouldnt be here.
            snaplist.append(datetime.now().strftime('daily-%Y%m%d%H%M'))

            # Do we need a weekly?
            if self.weekly_retention:
                weeklies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('weekly')]
                if weeklies:
                    latest = sorted(weeklies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (
                            datetime.now() - relativedelta(weeks=1)):
                        snaplist.append(
                            datetime.now().strftime('weekly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('weekly-%Y%m%d%H%M'))

            # Do we need a monthly?
            if self.monthly_retention:
                monthlies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('monthly')]
                if monthlies:
                    latest = sorted(monthlies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (
                            datetime.now() - relativedelta(months=1)):
                        snaplist.append(
                            datetime.now().strftime('monthly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('monthly-%Y%m%d%H%M'))

            # Do we need a yearly?
            if self.yearly_retention:
                yearlies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('yearly')]
                if yearlies:
                    latest = sorted(yearlies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (
                            datetime.now() - relativedelta(years=1)):
                        snaplist.append(
                            datetime.now().strftime('yearly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('yearly-%Y%m%d%H%M'))

        for snapname in snaplist:
            logger.info("Created: %s" % bfs.snapshot_create(
                self.dest_pool, self.hostgroup, self.friendly_name,
                snapname=snapname))

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

        return super().save(*args, **kwargs)


class BackupRun(models.Model):
    """
    Info about a single backup run. Some of these fields are duplicated
    in the Fileset model. We like those there too, so we use it to
    quickly sort those records.

    Runs with success==True show sensible info. For others you may need
    to take (some of) the values with a grain of salt.
    """
    fileset = models.ForeignKey(Fileset, on_delete=models.CASCADE)

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

    # TODO: do we want to store (a json blob of) the fileset config
    # (including transport?) as well?

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
            path, size = line.rsplit(':', 1)
            if path[0] == path[-1] == '"':
                path = path[1:-1]
            size = int(size.replace(',', ''))
            list_.append((path, size))
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
