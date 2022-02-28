import logging
import os
import shlex

from django.conf import settings
from django.db import connections, models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from planb.common.fields import FilelistField
from planb.common.subprocess2 import (
    CalledProcessError, argsjoin, check_output)
from planb.transport import AbstractTransport
from planb.utils import lazysetting

from .apps import TABLE_PREFIX
from .rsync import RSYNC_EXITCODES, RSYNC_HARMLESS_EXITCODES

logger = logging.getLogger(__name__)


class TransportChoices(models.PositiveSmallIntegerField):
    SSH = 0
    RSYNC = 1

    def __init__(self, *args, **kwargs):
        choices = (
            (self.SSH, _('ssh (default)')),
            (self.RSYNC, _('rsync (port 873)')),
        )
        super().__init__(default=self.SSH, choices=choices)


class Config(AbstractTransport):
    host = models.CharField(max_length=254)

    src_dir = models.CharField(max_length=254, default='/')
    includes = FilelistField(
        max_length=1023, default=lazysetting('PLANB_DEFAULT_INCLUDES'))
    excludes = FilelistField(
        max_length=1023, blank=True)

    transport = TransportChoices()
    user = models.CharField(
        max_length=254, default=lazysetting(
            'PLANB_RSYNC_USER', 'remotebackup'))

    use_sudo = models.BooleanField(default=True)
    use_ionice = models.BooleanField(default=True)

    rsync_path = models.CharField(
        max_length=31, default=lazysetting('PLANB_RSYNC_BIN'))
    ionice_path = models.CharField(
        max_length=31, default='/usr/bin/ionice', blank=True)

    # When files have legacy/Latin-1 encoding, you'll get rsync exit
    # code 23 and this message:
    #   rsync: recv_generator: failed to stat "...":
    #   Invalid or incomplete multibyte or wide character (84)
    # Solution, add: --iconv=utf8,latin1
    flags = models.CharField(
        max_length=511, blank=True, default='',
        help_text=_(
            'Default "", add "--no-perms --chmod=D0700,F600" '
            'for (windows) hosts without permission bits, add '
            '"--iconv=utf8,latin1" for hosts with files with legacy (Latin-1) '
            'encoding, add "--bwlimit=" for hosts with no bandwidth limit, '
            'add "--compress-choice=lz4" for newer compression.'))

    class Meta:
        db_table = TABLE_PREFIX  # or '{}_config'.format(TABLE_PREFIX)

    def __str__(self):
        return 'rsync transport {}'.format(self.host)

    def get_change_url(self):
        return reverse('admin:transport_rsync_config_change', args=(self.pk,))

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
        return ''.join(flag)

    def get_transport_ssh_known_hosts_d(self):
        # FIXME: assert that there is no nastiness in $HOME? This value
        # is placed in the rsync ssh options call later on.
        known_hosts_d = (
            os.path.join(os.environ.get('HOME', ''), '.ssh/known_hosts.d'))
        try:
            os.makedirs(known_hosts_d, 0o755)
        except FileExistsError:
            pass
        return known_hosts_d

    def get_transport_ssh_options(self):
        """
        Get ssh options to set a per-host known_hosts file, and to
        ignore host checking on the first run.

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
        known_hosts_d = self.get_transport_ssh_known_hosts_d()
        known_hosts_file = os.path.join(known_hosts_d, self.host)

        args = [
            '-o HashKnownHosts=no',
            '-o UserKnownHostsFile={}'.format(known_hosts_file),
        ]
        if os.path.exists(os.path.join(known_hosts_d, self.host)):
            # If the file exists, check the keys.
            args.append('-o StrictHostKeyChecking=yes')
        else:
            # If the file does not exist, create it and don't care
            # about the fingerprint.
            args.append('-o StrictHostKeyChecking=no')

        return ' '.join(args)

    def get_transport_ssh_uri(self):
        src_dir = (
            self.src_dir
            if self.src_dir.endswith('/')
            else '{}/'.format(self.src_dir))
        return '{o.user}@{o.host}:{src_dir}'.format(o=self, src_dir=src_dir)

    def get_transport_rsync_uri(self):
        return '{o.host}::{o.src_dir}'.format(o=self)

    def get_transport_args(self, remote_shell=None):
        if self.transport == TransportChoices.SSH:
            if not remote_shell:
                remote_shell = 'ssh'  # could also be 'ssh -l blah...'
            remote_shell = '--rsh={} {}'.format(
                remote_shell, self.get_transport_ssh_options())
            retval = (
                remote_shell,
                self.get_transport_ssh_rsync_path(),
                self.get_transport_ssh_uri())

        elif self.transport == TransportChoices.RSYNC:
            if remote_shell:
                raise NotImplementedError(remote_shell)
            retval = (self.get_transport_rsync_uri(),)

        else:
            raise NotImplementedError(
                'Unknown transport: %r' % (self.transport,))

        return retval

    def get_rsync_flags(self):
        """
        Take flags and split them. If there is a "-e ssh ...", we'll use
        that as remote_shell.
        """
        flags = shlex.split(self.flags)
        remote_shell = None

        if self.transport == TransportChoices.SSH:
            for idx, flag in enumerate(flags):
                if flag == '-e':
                    if (len(flags) > (idx + 1)
                            and flags[idx + 1].startswith('ssh ')):
                        remote_shell = flags[idx + 1]
                        flags = flags[0:idx] + flags[idx + 2:]
                        break
                    else:
                        raise NotImplementedError(
                            'parsing -e failed: {!r}'.format(flags))
                elif flag.startswith('--rsh='):
                    if flag.startswith('--rsh=ssh '):
                        remote_shell = flag[6:]
                        flags = flags[0:idx] + flags[idx + 1:]
                        break
                    else:
                        raise NotImplementedError(
                            'parsing --rsh= failed: {!r}'.format(flags))

        flags = tuple(flags)
        return flags, remote_shell

    def generate_rsync_command(self):
        def in_arg(arg, other_list):
            if arg.startswith('--') and '=' in arg:
                # Easy: --option=value
                arg = arg.split('=', 1)[0]
                arg += '='
                if any(i.startswith(arg) for i in other_list):
                    return True
            elif arg.startswith('--'):
                # Nothing to do: --option [or worse: --option value]
                pass
            elif arg.startswith('-'):
                assert False, 'single dash arguments not supported'
                # ... because we'd have to check the next argument
                # Hard: -o value
                # Hardest: -ovalue
            return False

        rsync_flags, remote_shell = self.get_rsync_flags()
        data_dir = self.fileset.get_dataset().get_data_path()

        simple_args = (
            settings.PLANB_RSYNC_BIN,
            '--delete',
            '--stats',
            # -a, --archive; equals -rlptgoD (no -H,-A,-X)
            '--recursive',  # -r
            '--links',      # -l
            # > rsync 3.2.3 is affected by lack of:
            # > https://github.com/WayneD/rsync/commit/
            # >   9dd62525f3b98d692e031f22c02be8f775966503
            # > see: https://bugs.gentoo.org/777483
            '--perms',      # -p
            '--times',      # -t
            # '--group' <-- not '-g'
            # '--owner' <-- not '-o'
            # '--numeric-ids' <-- only useful if we use group/owner
            '--devices',    # -D (won't work without root though)
            '--specials',   # -D
            # Work around rsync bug in 3.1.0. Possibly not needed when
            # we (also) use --whole-file.
            # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=741628
            '--block-size=131072',  # 128k == MAX_BLOCK_SIZE (1 << 17)
            # We rarely update files and we have fast link everywhere.
            # Don't spend time on checking/transferring partial files.
            '--whole-file',
            # Fix problems when we're not root, but we can download dirs
            # with improper perms because we're root remotely. Rsync
            # could set up dir structures where files inside cannot be
            # accessible anymore. Make sure our user has rx access.
            '--chmod=Du+rx',
            # Limit bandwidth a bit by default.
            '--bwlimit=10M')
        used_simple_args = tuple(
            arg for arg in simple_args if not in_arg(arg, rsync_flags))

        transport_args = self.get_transport_args(remote_shell=remote_shell)

        args = (
            used_simple_args
            + tuple(arg for arg in rsync_flags if arg != '--bwlimit=')  # hacks
            + self.create_exclude_string()
            + self.create_include_string()
            + ('--exclude=*',)
            + transport_args
            + (data_dir,))

        return args

    def run_transport(self):
        cmd = self.generate_rsync_command()
        logger.info(
            'Running %s: %s', self.fileset.friendly_name, argsjoin(cmd))

        # Close all DB connections before continuing with the rsync
        # command. Since it may take a while, the connection could get
        # dropped and we'd have issues later on.
        connections.close_all()

        stderr = []
        try:
            output = check_output(cmd, return_stderr=stderr).decode('utf-8')
            returncode = 0
        except CalledProcessError as e:
            returncode, output = e.returncode, e.output
            errstr = RSYNC_EXITCODES.get(returncode, 'Return code not matched')
            logger.warning(
                'code: %s\nmsg: %s\nexception: %s', returncode, errstr, str(e))
            if returncode not in RSYNC_HARMLESS_EXITCODES:
                raise

        logger.info(
            'Rsync exited with code %s for %s:'
            '\n\n(stdout)\n\n%s\n(stderr)\n\n%s',
            returncode, self.fileset.friendly_name, output,
            b'\n'.join(stderr).decode('utf-8', 'replace'))
