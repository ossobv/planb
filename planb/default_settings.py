import os

_DEFAULT_DIRS = tuple(
    'root etc home data srv var/backups var/spool/cron var/www usr/local/bin'
    .split(' '))
_DEFAULT_FILES = tuple(
    (i + '*') for i in  # files need a '*' in them
    'var/lib/dpkg/status var/lib/psdiff.db var/log/auth'.split(' '))
PLANB_DEFAULT_INCLUDES = ' '.join(sorted(_DEFAULT_DIRS + _DEFAULT_FILES))


TIME_ZONE = 'Europe/Amsterdam'
LANGUAGE_CODE = 'en-us'
DATETIME_FORMAT = SHORT_DATETIME_FORMAT = 'Y-m-d H:i'  # for admin-forms


USE_I18N = True
USE_L10N = True
USE_TZ = True  # must be True for Django-Q

# Static settings.
STATIC_URL = '/static/'

LOGIN_URL = '/accounts/login/'

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    # 'django.contrib.staticfiles.finders.DefaultStorageFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = None

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': (
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.request',
                'django.template.context_processors.static',
            ),
        },
    },
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'kleides_dssoclient.middleware.DssoLoginMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# TEMPLATE_CONTEXT_PROCESSORS = (
# )

ROOT_URLCONF = 'planb.urls'

INSTALLED_APPS = (
    # Main app first, so it can override templates.
    'planb',
    'planb.common',  # for loading templatetags
    'planb.transport_exec',
    'planb.transport_rsync',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.messages',
    'django.contrib.sessions',
    'django.contrib.sitemaps',
    'django.contrib.staticfiles',

    'django_q',
    'kleides_dssoclient',
)

AUTHENTICATION_BACKENDS = [
    # Users authenticated using the Dsso backend are added to the user
    # group named in the PLANB_USER_GROUP setting by the
    # PlanbDssoLoginBackend.
    # 'planb.backends.PlanbDssoLoginBackend',
    # Standalone/devoper-style auth:
    'django.contrib.auth.backends.ModelBackend',
]
PLANB_USER_GROUP = 'PlanB user'
KLEIDES_DSSO_ENDPOINT = None    # None if not using kleides_dssoclient auth

# FIXME: can we populate this automatically through INSTALLED_APPS?
# ... or we might want to use this to "order"/sort the preferred transports
PLANB_TRANSPORTS = [
    'transport_rsync.Config',   # common
    'transport_exec.Config',    # rare
]

# Q_CLUSTER_QUEUE is the queue the qcluster worker should process.
Q_MAIN_QUEUE = 'main'
Q_CLUSTER_QUEUE = os.environ.get('Q_CLUSTER_QUEUE', Q_MAIN_QUEUE)

# The worker queue for dutree tasks, limited to 1 worker. See how this is set
# in the bqcluster management command.
Q_DUTREE_QUEUE = 'dutree'
Q_DUTREE_WORKERS = 1

Q_CLUSTER = {
    'name': 'planb',    # redis prefix AND default broker (yuck!)
    'workers': 10,      # how many workers to process tasks simultaneously
    'timeout': 86300,   # almost a day
    'retry': 86400,     # an entire day (needed??)
    'catch_up': False,  # no catching up of missed scheduled tasks
    'compress': False,  # don't care about payload size
    # The save limit must exceed the amount of enabled filesets * 2 + a little.
    # If the task result cannot be saved the hook will not trigger and
    # this will cause the backup_done signal to be skipped.
    'save_limit': 1000,  # store 1000 successful jobs, drop older
    'label': 'Task Queue',  # seen in Django Admin
    'scheduler': True,  # Schedule on default queue
    'redis': {
        'host': '127.0.0.1',
        'port': 6379,
        'db': 0,
    },
}


LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'formatters': {
        'simple': {
            'format': (
                '%(asctime)s [planb/%(process)5d] '
                '[%(levelname)-3.3s] %(message)s (%(name)s)'),
        },
        'notime': {
            'format': '%(name)s - %(levelname)s/%(process)s - %(message)s',
        },
    },
    'handlers': {
        'null': {
            'level': 'DEBUG',
            'class': 'logging.NullHandler',
        },
        'mail_admins_err': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'planb.common.log2.AdminEmailHandler'  # django.utils.log
        },
        'mail_admins_warn': {
            'level': 'WARNING',
            'filters': ['require_debug_false'],
            'class': 'planb.common.log2.AdminEmailHandler'  # django.utils.log
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
            'filters': ['require_debug_true'],
        },
        # 'gelf': {
        #     'class': 'graypy.GELFHandler',
        #     'host': '10.x.x.x',
        #     'port': 12221,
        # },
        'logfile': {
            'level': 'INFO',
            'class': 'logging.handlers.WatchedFileHandler',
            'formatter': 'simple',
            'filename': '/var/log/planb/core.log',
            # Delay, so management commands don't try to open these
            # unless they have to.
            'delay': True,
        },
        'djangoqlogfile': {
            'level': 'DEBUG',
            'class': 'logging.handlers.WatchedFileHandler',
            'formatter': 'simple',
            'filename': '/var/log/planb/queue.log',
            # Delay, so management commands don't try to open these
            # unless they have to.
            'delay': True,
        },
    },
    'loggers': {
        'planb': {
            'handlers': ['console', 'logfile', 'mail_admins_err'],
            'level': 'DEBUG',
            'propagate': False,
        },
        '': {
            'handlers': ['mail_admins_warn'],
            'level': 'WARNING',
        },
        # Let all other handlers below propagate on to here so we can send mail
        # for all WARNINGs.
        'django-q': {
            'handlers': ['djangoqlogfile'],
            'level': 'DEBUG',
        },
        'django': {
            'handlers': ['console'],
        },
        'py.warnings': {
            'handlers': ['console'],
        },
    }
}

PLANB_PREFIX = 'planb'
PLANB_RETENTION = '2y,12m,4w,16d'
PLANB_BLACKLIST_HOURS = '9-17'
