import logging
import os
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.core.mail import mail_admins
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import ugettext as _

from planb.common.subprocess2 import (
    CalledProcessError, check_call, check_output)
from planb.signals import backup_done
from planb.storage.zfs import Zfs

from .fields import FilelistField, MultiEmailField

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    getproctitle = setproctitle = None

DEFAULT_DIRS = tuple(
    'root etc home data srv var/backups var/spool/cron var/www usr/local/bin'
    .split(' '))
DEFAULT_FILES = tuple(
    (i + '*') for i in  # files need a '*' in them
    'var/lib/dpkg/status var/lib/psdiff.db'.split(' '))
DEFAULT_INCLUDES = ' '.join(sorted(DEFAULT_DIRS + DEFAULT_FILES))


def get_pools():
    pools = []
    for name, pool, bfs in settings.STORAGE_POOLS:
        assert bfs == 'zfs', bfs
        val1 = float(check_output(
            [settings.SUDO_BIN, settings.ZFS_BIN, 'get', '-Hpo', 'value',
             'used', pool]).strip())
        val2 = float(check_output(
            [settings.SUDO_BIN, settings.ZFS_BIN, 'get', '-Hpo', 'value',
             'available', pool]).strip())
        pct = '{pct:.0f}%'.format(pct=(100 * (val1 / (val1 + val2))))
        available = int(val2 / 1024 / 1024 / 1024)
        pools.append((pool, '{}, {}G free ({} used)'.format(
            name, available, pct)))
    return tuple(pools)


logger = logging.getLogger(__name__)

bfs = Zfs(binary=settings.ZFS_BIN, sudobin=settings.SUDO_BIN)

valid_rsync_codes = (
    24,  # vanished source files
)


class HostGroup(models.Model):
    name = models.CharField(max_length=63, unique=True)
    notify_email = MultiEmailField(help_text=_('Use a newline per '
                                   'emailaddress'), blank=True, null=True)

    def get_backup_info(self):
        results = {}
        for hostconfig in self.hostconfigs.all():
            results[hostconfig.friendly_name] = bfs.parse_backup_sizes(
                hostconfig.dest_pool, self.name, hostconfig.friendly_name,
                hostconfig.date_complete)
            results[hostconfig.friendly_name]['enabled'] = hostconfig.enabled
        return results

    def __str__(self):
        return self.name


class HostConfig(models.Model):
    TRANSPORT_SSH = 0
    TRANSPORT_RSYNC = 1
    TRANSPORT_CHOICES = (
        (TRANSPORT_SSH, _('ssh (default)')),
        (TRANSPORT_RSYNC, _('rsync (port 873)')),
    )

    friendly_name = models.CharField(
        # FIXME: should be unique with hostgroup?
        verbose_name=_('Name'), max_length=63, unique=True,
        help_text=_('Short name, should be unique per host group.'))
    host = models.CharField(max_length=254)
    description = models.TextField(blank=True, help_text=_(
        'Quick description/tips. Use the first line for labels/tags.'))
    transport = models.PositiveSmallIntegerField(
        choices=TRANSPORT_CHOICES, default=TRANSPORT_CHOICES[0][0])
    user = models.CharField(max_length=254, default='root')
    src_dir = models.CharField(max_length=254, default='/')
    dest_pool = models.CharField(max_length=254, choices=())  # set in forms.py
    retention = models.IntegerField(
        verbose_name=_('Daily retention'), default=15,
        help_text=_('How many days do we keep?'))
    rsync_path = models.CharField(max_length=31, default='/usr/bin/rsync')
    ionice_path = models.CharField(
        max_length=31, default='/usr/bin/ionice', blank=True)
    # When files have legacy/Latin-1 encoding, you'll get rsync exit
    # code 23 and this message:
    #   rsync: recv_generator: failed to stat "...":
    #   Invalid or incomplete multibyte or wide character (84)
    # Solution, add: --iconv=utf8,latin1
    flags = models.CharField(
        max_length=511, default='-az --numeric-ids --stats --delete',
        help_text=_(
            'Default "-az --delete", add "--no-perms --chmod=D0700,F600" '
            'for (windows) hosts without permission bits, add '
            '"--iconv=utf8,latin1" for hosts with files with legacy (Latin-1) '
            'encoding.'))
    includes = FilelistField(max_length=1023, default=DEFAULT_INCLUDES)
    excludes = FilelistField(max_length=1023, blank=True)
    running = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)
    date_complete = models.DateTimeField(
        'Complete date', default=datetime(1970, 1, 2, tzinfo=timezone.utc))
    complete_duration = models.PositiveIntegerField(
        'Time', default=0,  # this value may vary..
        help_text=_('Duration in seconds of last successful job.'))
    enabled = models.BooleanField(default=True)
    queued = models.BooleanField(default=False)
    failure_datetime = models.DateTimeField(blank=True, null=True)
    hostgroup = models.ForeignKey(HostGroup, related_name='hostconfigs')
    use_sudo = models.BooleanField(default=False)
    use_ionice = models.BooleanField(default=False)
    file_to_check = models.CharField(
        max_length=255, default='var/log/kern.log',
        blank=True, null=True)
    keep_weekly = models.BooleanField(default=False)
    keep_monthly = models.BooleanField(default=False)
    keep_yearly = models.BooleanField(default=False)
    weekly_retention = models.IntegerField(
        default=3, blank=True, null=True,
        help_text=_('How many weekly\'s do we need to keep?'))
    monthly_retention = models.IntegerField(
        default=11, blank=True, null=True,
        help_text=_('How many monthly\'s do we need to keep?'))
    yearly_retention = models.IntegerField(
        default=1, blank=True, null=True,
        help_text=_('How many yearly\'s do we need to keep?'))
    backup_size_mb = models.PositiveIntegerField(
        verbose_name=_('Size'), default=0, db_index=True,
        help_text=_('Estimated total backup size in MiB.'))

    def __str__(self):
        return '{} ({})'.format(self.friendly_name, self.id)

    @property
    def identifier(self):
        return '{}-{}'.format(self.hostgroup.name, self.friendly_name)

    def clone(self, **override):
        # See: https://github.com/django/django/commit/a97ecfdea8
        copy = self.__class__.objects.get(pk=self.pk)
        copy.pk = None
        copy.date_complete = datetime(1970, 1, 2, tzinfo=timezone.utc)
        copy.failure_datetime = None
        copy.queued = copy.running = False
        copy.complete_duration = 0
        copy.backup_size_mb = 0

        # Use the overrides.
        for key, value in override.items():
            setattr(copy, key, value)

        copy.save()
        return copy

    def can_backup(self):
        if not self.enabled:
            return False
        if (self.date_complete.date() >= date.today() and
                self.failure_datetime is None):
            return False
        # this one is heavy, avoid it using the date check above..
        if not bfs.can_backup(
                self.dest_pool, self.hostgroup, self.friendly_name):
            return False
        self.refresh_from_db()
        if self.running:
            return False
        return True

    def snapshot_rotate(self):
        return bfs.snapshots_rotate(
            self.dest_pool, self.hostgroup,
            self.friendly_name,
            daily_retention=self.retention,
            weekly_retention=self.weekly_retention,
            monthly_retention=self.monthly_retention,
            yearly_retention=self.yearly_retention)

    def snapshot_create(self):
        # Add logica what kind of snapshot
        # First we need to know what we have
        snapshots = bfs.snapshots_get(
            self.dest_pool, self.hostgroup, self.friendly_name)

        snaplist = []
        if not snapshots:
            snaplist.append(datetime.now().strftime('daily-%Y%m%d%H%M'))
            if self.keep_weekly:
                snaplist.append(datetime.now().strftime('weekly-%Y%m%d%H%M'))
            if self.keep_monthly:
                snaplist.append(datetime.now().strftime('monthly-%Y%m%d%H%M'))
            if self.keep_yearly:
                snaplist.append(datetime.now().strftime('yearly-%Y%m%d%H%M'))
        else:
            # Do we need a daily? We do, otherwise we wouldnt be here
            snaplist.append(datetime.now().strftime('daily-%Y%m%d%H%M'))

            # Do we need a weekly?
            if self.keep_weekly:
                weeklies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('weekly')]
                if weeklies:
                    latest = sorted(weeklies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (datetime.now() -
                                        relativedelta(weeks=1)):
                        snaplist.append(
                            datetime.now().strftime('weekly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('weekly-%Y%m%d%H%M'))

            # Do we need a monthly?
            if self.keep_monthly:
                monthlies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('monthly')]
                if monthlies:
                    latest = sorted(monthlies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (datetime.now() -
                                        relativedelta(months=1)):
                        snaplist.append(
                            datetime.now().strftime('monthly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('monthly-%Y%m%d%H%M'))

            # Do we need a yearly?
            if self.keep_yearly:
                yearlies = [
                    x for x in snapshots
                    if x.split('@')[1].startswith('yearly')]
                if yearlies:
                    latest = sorted(yearlies)[-1]
                    dts = latest.split('@', 1)[1].split('-', 1)[1]
                    datetimestamp = datetime.strptime(dts, '%Y%m%d%H%M')
                    if datetimestamp < (datetime.now() -
                                        relativedelta(years=1)):
                        snaplist.append(
                            datetime.now().strftime('yearly-%Y%m%d%H%M'))
                else:
                    snaplist.append(
                        datetime.now().strftime('yearly-%Y%m%d%H%M'))

        for snapname in snaplist:
            logger.info("Created: %s" % bfs.snapshot_create(
                self.dest_pool, self.hostgroup, self.friendly_name,
                snapname=snapname))

    def create_exclude_string(self):
        exclude_list = []
        if self.excludes:
            for piece in self.excludes.split():
                exclude_list.append('--exclude=%s' % piece)
        return tuple(exclude_list)

    def create_include_string(self):
        # Create list of includes, with parent-paths included before the
        # includes.
        include_list = []
        for include in self.includes.split():
            included_parts = ''
            elems = include.split('/')

            # Add parent paths.
            for part in elems[0:-1]:
                included_parts = '/'.join([included_parts, part]).lstrip('/')
                include_list.append(included_parts + '/')

            # Add final path. If the basename contains a '*', we treat
            # it as file, otherwise we treat is as dir and add '/***'.
            included_parts = '/'.join([included_parts, elems[-1]]).lstrip('/')
            if '*' in included_parts:
                include_list.append(included_parts)
            else:
                include_list.append(included_parts + '/***')

        # Sorted/uniqued include list, removing duplicates.
        include_list = sorted(set(include_list))

        # Return values with '--include=' prepended.
        return tuple(('--include=' + i) for i in include_list)

    def get_transport_ssh_rsync_path(self):
        """
        Return --rsync-path=... for the ssh-transport.

        May optionally add 'sudo' and 'ionice'.
        """
        flag = ['--rsync-path=']
        if self.use_sudo:
            flag.append('sudo ')
        if self.use_ionice:
            flag.append(self.ionice_path)
            flag.append(' -c2 -n7 ')
        flag.append(self.rsync_path)
        return (''.join(flag),)

    def get_transport_ssh_options(self):
        """
        Get rsync '-e' option which specifies ssh binary and arguments,
        used to set a per-host known_hosts file, and ignore host checking
        on the first run.

        For compatibility with this, you may want this function in your
        planb user .bashrc::

            ssh() {
                for arg in "$@"; do
                    case $arg in
                    -*) ;;
                    *) break ;;
                    esac
                done
                if test -n "$arg"; then
                    host=${arg##*@}
                    /usr/bin/ssh -o HashKnownHosts=no \\
                      -o UserKnownHostsFile=$HOME/.ssh/known_hosts.d/$host "$@"
                else
                    /usr/bin/ssh "$@"
                fi
            }
        """
        option = '-e'
        binary = 'ssh'
        known_hosts_d = (
            os.path.join(os.environ.get('HOME', ''), '.ssh/known_hosts.d'))
        try:
            os.makedirs(known_hosts_d, 0o755)
        except FileExistsError:
            pass
        known_hosts_file = os.path.join(known_hosts_d, self.host)
        args = [
            '-o HashKnownHosts=no',
            '-o UserKnownHostsFile=%s' % (known_hosts_file,),
        ]
        if os.path.exists(os.path.join(known_hosts_d, self.host)):
            # If the file exists, check the keys.
            args.append('-o StrictHostKeyChecking=yes')
        else:
            # If the file does not exist, create it and don't care
            # about the fingerprint.
            args.append('-o StrictHostKeyChecking=no')
        return (
            '%(option)s%(binary)s %(args)s' % {
                'option': option, 'binary': binary, 'args': ' '.join(args)},)

    def get_transport_ssh_uri(self):
        return ('%s@%s:%s' % (self.user, self.host, self.src_dir),)

    def get_transport_rsync_uri(self):
        return ('%s::%s' % (self.host, self.src_dir),)

    def get_transport_uri(self):
        if self.transport == self.TRANSPORT_SSH:
            return (
                self.get_transport_ssh_rsync_path() +
                self.get_transport_ssh_options() +
                self.get_transport_ssh_uri())
        elif self.transport == self.TRANSPORT_RSYNC:
            return self.get_transport_rsync_uri()
        else:
            raise NotImplementedError(
                'Unknown transport: %r' % (self.transport,))

    def generate_rsync_command(self):
        flags = tuple(self.flags.split())
        data_dir = (
            bfs.data_dir_get(
                self.dest_pool, str(self.hostgroup), self.friendly_name),)

        args = (
            (settings.RSYNC_BIN,) +
            # Work around rsync bug in 3.1.0:
            # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=741628
            ('--block-size=65536',) +
            flags +
            self.create_exclude_string() +
            self.create_include_string() +
            ('--exclude=*', '--bwlimit=10000') +
            self.get_transport_uri() +
            data_dir)

        return args

    def log_error_message(self, msg, exception):
        msg = 'code: {0}\nmsg: {1}\nexception: {2}'.format(
              exception.returncode, msg, str(exception))
        # Don't use ERROR, all errors get mailed. Use WARNING instead.
        logging.warning(msg)

    def rsync_exit_codes(self, e):
        code = e.returncode
        if code == 1:
            self.log_error_message('Syntax or usage error', e)
        elif code == 2:
            self.log_error_message('Protocol incompatibility', e)
        elif code == 3:
            self.log_error_message('Errors selecting input/output files, '
                                   'dirs', e)
        elif code == 4:
            self.log_error_message('Requested action not supported: an '
                                   'attempt was made to manipulate '
                                   '64-bit files on a platform that cannot '
                                   'support them; or an option was specified '
                                   'that is supported by the client and not '
                                   'by the server.', e)
        elif code == 5:
            self.log_error_message('Error starting client-server protocol', e)
        elif code == 6:
            self.log_error_message('Daemon unable to append to log-file', e)
        elif code == 10:
            self.log_error_message('Error in socket I/O', e)
        elif code == 11:
            self.log_error_message('Error in file I/O', e)
        elif code == 12:
            self.log_error_message('Error in rsync protocol data stream', e)
        elif code == 13:
            self.log_error_message('Errors with program diagnostics', e)
        elif code == 14:
            self.log_error_message('Error in IPC code', e)
        elif code == 20:
            self.log_error_message('Received SIGUSR1 or SIGINT', e)
        elif code == 21:
            self.log_error_message('Some error returned by waitpid()', e)
        elif code == 22:
            self.log_error_message('Error allocating core memory buffers', e)
        elif code == 23:
            self.log_error_message('Partial transfer due to error', e)
        elif code == 24:
            self.log_error_message('Partial transfer due to vanished source '
                                   'files', e)
        elif code == 25:
            self.log_error_message('The --max-delete limit stopped '
                                   'deletions', e)
        elif code == 30:
            self.log_error_message('Timeout in data send/receive', e)
        elif code == 35:
            self.log_error_message('Timeout waiting for daemon connection', e)
        elif code == 255:
            self.log_error_message('Unspecified error', e)
        else:
            self.log_error_message('Returncode not matched', e)

    def run_rsync(self):
        cmd = self.generate_rsync_command()
        logger.info('Running %s: %s' % (self.friendly_name, ' '.join(cmd)))
        try:
            output = check_output(cmd).decode('utf-8')
            returncode = 0
        except CalledProcessError as e:
            self.rsync_exit_codes(e)
            output = e.output
            returncode = e.returncode
            if returncode not in valid_rsync_codes:
                raise

        logger.info(
            'Rsync exited with code %s for %s. Output: %s' % (
                returncode, self.friendly_name, output))

    def run(self):
        if setproctitle:
            oldproctitle = getproctitle()
            setproctitle('[backing up %d: %s]' % (self.pk, self.friendly_name))

        try:
            self.run_rsync()
            self.snapshot_rotate()
            self.snapshot_create()

            # Atomic update of size.
            size = bfs.parse_backup_sizes(
                self.dest_pool, self.hostgroup.name, self.friendly_name,
                self.date_complete)['size']
            size_mb = size[0:-6] or '0'  # :P
            HostConfig.objects.filter(pk=self.pk).update(
                backup_size_mb=size_mb)

            # Send signal that we're done.
            self.signal_done(True)

        except:
            # Send signal that we've failed.
            self.signal_done(False)
            # Propagate.
            raise

        finally:
            if setproctitle:
                setproctitle(oldproctitle)

    def signal_done(self, success):
        instance = HostConfig.objects.get(pk=self.pk)
        # Using send_robust, because we do not want user-code to mess up
        # the rest of our state.
        backup_done.send_robust(
            sender=self.__class__, hostconfig=instance, success=success)

    def save(self, *args, **kwargs):
        # Notify the same users who get ERROR / Success for backups that
        # the job was disabled/re-enabled.
        if self.pk:
            old_enabled = HostConfig.objects.values_list(
                'enabled', flat=True).get(pk=self.pk)
            if self.enabled != old_enabled:
                mail_admins(
                    '{} backup: {}'.format(
                        'Enabled' if self.enabled else 'Disabled', self),
                    'Toggled enabled-flag on {}.\n'.format(self))

        return super(HostConfig, self).save(*args, **kwargs)


@receiver(post_save, sender=HostConfig)
def create_dataset(sender, instance, created, *args, **kwargs):
    data_dir_name = bfs.data_dir_get(
        instance.dest_pool, instance.hostgroup,
        instance.friendly_name)

    if data_dir_name is None:
        bfs.data_dir_create(
            instance.dest_pool, instance.hostgroup,
            instance.friendly_name)
        data_dir_name = bfs.data_dir_get(
            instance.dest_pool, instance.hostgroup,
            instance.friendly_name)
        try:
            # Create the /data subdir.
            os.makedirs(data_dir_name, 0o755)
        except FileExistsError:
            pass

    if not os.path.exists(data_dir_name):
        # Even if we have user-powers on /dev/zfs, we still cannot call
        # all commands.
        # $ /sbin/zfs mount rpool/BACKUP/example-example
        # mount: only root can use "--options" option
        # cannot mount 'rpool/BACKUP/example-example': Invalid argument
        # Might as well use sudo everywhere then.
        dataset_name = bfs.get_dataset_name(
            instance.dest_pool, instance.hostgroup,
            instance.friendly_name)
        check_call((settings.SUDO_BIN, bfs.binary, 'mount', dataset_name))
