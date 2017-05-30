import logging
import os


class AddPidFilter(logging.Filter):
    def filter(self, record):
        record.pid = os.getpid()
        return True


# The app/settings dir:
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# The project root:
_ROOT_DIR = os.path.dirname(PROJECT_DIR)

TIME_ZONE = 'Europe/Amsterdam'
LANGUAGE_CODE = 'en_US'

SITE_ID = 1

USE_I18N = True
USE_L10N = True
USE_TZ = True  # must be True for Django-Q

# Media/static settings.
MEDIA_ROOT = os.path.join(_ROOT_DIR, 'media')
MEDIA_URL = '/media/'
STATIC_ROOT = os.path.join(_ROOT_DIR, 'static')
STATIC_URL = '/static/'

LOGIN_URL = '/accounts/login/'

# Additional locations of static files.
STATICFILES_DIRS = (
)

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    # 'django.contrib.staticfiles.finders.DefaultStorageFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = None

# List of callables that know how to import templates from various sources.
TEMPLATE_LOADERS = (
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
    # 'django.template.loaders.eggs.Loader',
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
)

TEMPLATE_CONTEXT_PROCESSORS = (
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
    'django.core.context_processors.debug',
    'django.core.context_processors.i18n',
    'django.core.context_processors.static',
    'django.core.context_processors.media',
    'django.core.context_processors.request',
    'django.core.context_processors.tz',
)

ROOT_URLCONF = 'planb.urls'

TEMPLATE_DIRS = (
    os.path.join(_ROOT_DIR, 'templates'),
)

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sitemaps',
    'django.contrib.staticfiles',

    'django_q',

    'planb',
)

Q_CLUSTER = {
    'name': 'PlanB',
    'workers': 5,
    'timeout': 86300,   # almost a day
    'retry': 86400,     # an entire day (needed??)
    'catch_up': False,  # no catching up of missed scheduled tasks
    'compress': False,  # don't care about payload size
    'save_limit': 250,  # store 250 succesful jobs, drop older..
    'label': 'Task Queue',  # seen in Django Admin
    'redis': {
        'host': '127.0.0.1',
        'port': 6379,
        'db': 5,
    },
}


LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'filters': {
        'addpid': {
            '()': AddPidFilter,
        },
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
                '%(asctime)s - %(name)s - %(levelname)s/%(pid)s - '
                '%(message)s'),
        },
        'notime': {
            'format': '%(name)s - %(levelname)s/%(pid)s - %(message)s',
        },
    },
    'handlers': {
        'null': {
            'level': 'DEBUG',
            'class': 'logging.NullHandler',
            'filters': ['addpid'],
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler'
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
            'filters': ['addpid'],
        },
        # 'gelf': {
        #     'class': 'graypy.GELFHandler',
        #     'host': '10.x.x.x',
        #     'port': 12221,
        #     'filters': ['addpid'],
        # },
        'logfile': {
            'level': 'DEBUG',
            'class': 'logging.handlers.WatchedFileHandler',
            'formatter': 'simple',
            'filename': '/var/log/planb/core.log',
            'filters': ['addpid'],
            # Delay, so management commands don't try to open these
            # unless they have to.
            'delay': True,
        },
        'djangoqlogfile': {
            'level': 'DEBUG',
            'class': 'logging.handlers.WatchedFileHandler',
            'formatter': 'simple',
            'filename': '/var/log/planb/queue.log',
            'filters': ['addpid'],
            # Delay, so management commands don't try to open these
            # unless they have to.
            'delay': True,
        },
    },
    'loggers': {
        '': {
            'handlers': ['mail_admins'],
            'level': 'ERROR',
        },
        # Let the handlers below propagate on to here so we can send
        # mail for all ERRORs.
        'planb': {
            'handlers': ['console', 'logfile'],
            'level': 'DEBUG',
        },
        'django-q': {
            'handlers': ['djangoqlogfile'],
            'level': 'DEBUG',
        },
        'libs': {
            'handlers': ['console', 'logfile'],
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
