import logging
import os

from django.db import connections, models
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from planb.common.fields import CommandField
from planb.common.subprocess2 import CalledProcessError, check_output

from .apps import TABLE_PREFIX

logger = logging.getLogger(__name__)


class Config(models.Model):
    fileset = models.OneToOneField(
        'planb.Fileset', on_delete=models.CASCADE, related_name='+')

    transport_command = CommandField(help_text=_(  # FIXME: add env docs
        'Program to run to do the transport (data import). It is '
        'split by spaces and fed to execve(). '
        'Useful variables are available in the environment.'))

    class Meta:
        db_table = TABLE_PREFIX  # or '{}_config'.format(TABLE_PREFIX)

    def __str__(self):
        return '{}: exec transport'.format(self.fileset)

    def get_change_url(self):
        return reverse('admin:transport_exec_config_change', args=(self.pk,))

    def generate_cmd(self):
        return self.transport_command.strip().split()

    def generate_env(self):
        env = {}

        # Don't blindly keep all env. We don't want e.g. PYTHONPATH because it
        # might be some virtual-envy python that has no access to where we want
        # to be.
        keep_env = (
            # Mandatory:
            'PATH',
            # Nice to have for shell apps:
            'HOME', 'PWD', 'SHELL', 'USER',
            # #'LANG', 'TZ',
            # Systemd/logging stuff:
            # #'JOURNAL_STREAM', 'LOGNAME', 'INVOCATION_ID',
        )
        for key in keep_env:
            if key in os.environ:
                env[key] = os.environ[key]

        # Add our own env.
        env['planb_fileset_id'] = str(self.fileset.id)
        env['planb_fileset_friendly_name'] = self.fileset.friendly_name
        env['planb_storage_destination'] = (
            self.fileset.get_dataset().get_data_path())

        return env

    def run_transport(self):
        # FIXME: duplicate code with transport_rsync.Config.run_transport()
        cmd = self.generate_cmd()
        env = self.generate_env()
        try:
            logger.info(
                'Running %s: %s', self.fileset.friendly_name, ' '.join(cmd))
        except Exception:
            logger.error('[%s]', repr(cmd))
            raise

        # Close all DB connections before continuing with the rsync
        # command. Since it may take a while, the connection could get
        # dropped and we'd have issues later on.
        connections.close_all()

        stderr = []
        try:
            # FIXME: do we want timeout handling here?
            output = check_output(
                cmd, env=env, return_stderr=stderr).decode('utf-8')
        except CalledProcessError as e:
            logging.warning(
                'Failure during exec %r: %s', ' '.join(cmd), str(e))
            raise

        logger.info(
            'Exec success for %s transport:\n\n(stdout)\n\n%s\n(stderr)\n\n%s',
            self.fileset.friendly_name, output,
            b'\n'.join(stderr).decode('utf-8', 'replace'))
