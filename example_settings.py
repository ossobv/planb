from planb.default_settings import *  # noqa

# Remember that DEBUG=True causes error-mails to not get sent, while
# successmails still get sent. This should probably be fixed. (FIXME)
# DEBUG = True

# Set the default paths
PLANB_SUDO_BIN = '/usr/bin/sudo'
PLANB_ZFS_BIN = '/sbin/zfs'
PLANB_RSYNC_BIN = '/usr/bin/rsync'

# Provide filesystem name + internal name. For ZFS that is "name", and "zfs
# path". The first one will be the default.
PLANB_STORAGE_POOLS = (
    ('Pool I', 'rpool/BACKUP', 'zfs'),
)

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

ALLOWED_HOSTS = ('planb', 'planb.example.com')

# Please replace this with the output of: pwgen -ys 58
SECRET_KEY = r'''pwgen -ys 58'''

STATIC_ROOT = '/srv/http/planb.example.com/static'
