from planb.default_settings import *  # noqa
from planb.default_settings import LOGGING  # fix flake warning

# Remember that DEBUG=True causes error-mails to not get sent, while
# successmails still get sent. This should probably be fixed. (FIXME)
# DEBUG = True

# Set the default paths
PLANB_SUDO_BIN = '/usr/bin/sudo'
PLANB_ZFS_BIN = '/sbin/zfs'
PLANB_RSYNC_BIN = '/usr/bin/rsync'

# Disable during dev?
# PLANB_SUDO_BIN = '/bin/true'
# PLANB_ZFS_BIN = '/bin/true'

# Globally unique identifier. Can be used by PlanB to mark snapshots as "owned"
# by this instance.
PLANB_GUID = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'  # use uuidgen(1) here

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
        'POOLNAME': 'tank/BACKUP',
        'DATASETKEYS': False,  # enable for ZFS encryption per dataset (safer!)
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
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'FIXME',    # Or path to database file if using sqlite3.
        'USER': 'FIXME',    # Not used with sqlite3.
        'PASSWORD': 'FIXMEFIXMEFIXME',   # Not used with sqlite3.
        'HOST': '',         # Empty for localhost. Not used with sqlite3.
        'PORT': '',         # Empty for default. Not used with sqlite3.
        'OPTIONS': {
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# If you want to log to a local directory instead of the default
# /var/log/planb/ then enable this:
if False:
    for key, handler in LOGGING['handlers'].items():
        if handler.get('filename', '').startswith('/var/log/planb/'):
            handler['filename'] = 'logs/{}'.format(handler['filename'][15:])

ALLOWED_HOSTS = ('planb', 'planb.example.com')

# Please replace this with the output of: pwgen -ys 58
SECRET_KEY = r'''pwgen -ys 58'''

STATIC_ROOT = '/srv/http/planb.example.com/static'

if True:
    # Regular auth.
    AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
else:
    # Auth using a Discourse Single-Sign-On (DSSO) server:
    # https://github.com/ossobv/kleides-dssoclient
    AUTHENTICATION_BACKENDS = ['planb.backends.PlanbDssoLoginBackend']
    KLEIDES_DSSO_ENDPOINT = 'https://SSO_SERVER/sso/'
    KLEIDES_DSSO_SHARED_KEY = 'oh-sso-very-very-secret'
