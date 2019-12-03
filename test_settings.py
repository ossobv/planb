from planb.default_settings import *  # noqa
from planb.default_settings import LOGGING, Q_CLUSTER

# Remember that DEBUG=True causes error-mails to not get sent, while
# successmails still get sent. This should probably be fixed. (FIXME)
# DEBUG = True

# Set the default paths
PLANB_SUDO_BIN = '/usr/bin/sudo'
PLANB_ZFS_BIN = '/sbin/zfs'
PLANB_RSYNC_BIN = '/usr/bin/rsync'

# Disable during dev?
PLANB_SUDO_BIN = '/bin/echo'
PLANB_ZFS_BIN = '/bin/echo'

# Configure storage pools.
# Name and engine are required.
# The config keys are passed to the Storage class in the config parameter.
# Note: If you migrate from an old planb you must ensure all fileset.dest_pool
# values map to a storage pool. e.g.
# PLANB_STORAGE_POOLS['tank'] = <your zfs config for dest_pool=tank>
PLANB_STORAGE_POOLS = {
    'dummy': {
        'ENGINE': 'planb.storage.dummy.DummyStorage',
        'NAME': 'Pool I',
    },
    'zfs': {
        'ENGINE': 'planb.storage.zfs.ZfsStorage',
        'NAME': 'ZFS Pool',
        'BINARY': PLANB_ZFS_BIN,
        'SUDOBIN': PLANB_SUDO_BIN,
        'POOLNAME': 'tank',
    },
}


MANAGERS = ADMINS = (
    # ('My Name', 'myname@example.com'),
)
DEFAULT_FROM_EMAIL = 'support@example.com'
SERVER_EMAIL = 'planb@example.com'
EMAIL_SUBJECT_PREFIX = '[PlanB] '

COMPANY_NAME = 'Example Company'
COMPANY_EMAIL = 'support@example.com'

# MySQL config example:
#
# SQL> set names utf8;
# SQL> create database planb;
# SQL> grant all on planb.* to planb identified by 'FIXMEFIXMEFIXME';

DATABASES = {
    'default': {
        # Choose 'postgresql_psycopg2', 'mysql', 'sqlite3' or 'oracle'.
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',    # Or path to database file if using sqlite3.
        'USER': '',    # Not used with sqlite3.
        'PASSWORD': '',   # Not used with sqlite3.
        'HOST': '',         # Empty for localhost. Not used with sqlite3.
        'PORT': '',         # Empty for default. Not used with sqlite3.
        'OPTIONS': {},
    }
}

# Replace file logging with output to stderr.
for key, handler in LOGGING['handlers'].items():
    if handler['class'] == 'logging.handlers.WatchedFileHandler':
        handler['class'] = 'logging.StreamHandler'
        del handler['filename']
        del handler['delay']

ALLOWED_HOSTS = ('testserver',)

SECRET_KEY = 'T3$TK3Y'

#STATIC_ROOT = '/srv/http/planb.example.com/static'

AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']

# XXX synchronous mode isn't fully supported by django-q and causes problems
# with transactions in the calling context.
#Q_CLUSTER['sync'] = True
