from planb.default_settings import *  # noqa

# TEMPLATE_DEBUG = DEBUG = TESTING = True

MANAGERS = ADMINS = (
    # ('My Name', 'myname@example.com'),
)
SERVER_EMAIL = 'planb@example.com'
EMAIL_SUBJECT_PREFIX = '[PlanB] '

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

# Use redis a broker
BROKER_URL = 'redis://localhost:6379/0'

# Set the default paths
ZFS_BIN = '/sbin/zfs'
RSYNC_BIN = '/usr/bin/rsync'

# Provide filesystem name + internal name. For ZFS that is "name", and "zfs
# path". The first one will be the default.
STORAGE_POOLS = (
    ('Pool I', 'rpool/BACKUP', 'zfs'),
)

# Please replace this with the output of: pwgen -ys 58
SECRET_KEY = r'''pwgen -ys 58'''
