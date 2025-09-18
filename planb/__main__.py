from importlib.metadata import (
    PackageNotFoundError, version as metadata_version)
import os
import sys

from django import get_version as get_django_version


def try_load_envvars(filenames):
    """
    Load the first available envvars file.
    """
    for envfile in filenames:
        if envfile is not None:
            try:
                fp = open(envfile)
            except Exception:
                pass
            else:
                try:
                    load_envvars(fp)
                finally:
                    fp.close()
                break
    else:
        raise ValueError('no envvars file to load: please set PLANB_ENVFILE')


def load_envvars(fp):
    """
    Load USER, PYTHONPATH and DJANGO_SETTINGS_MODULE from envvars file.

    We expect something like:
    - USER=planb
    - PYTHONPATH=/etc/planb
    - DJANGO_SETTINGS_MODULE=settings
    """
    for line in fp:
        line = line.strip()
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            if key == 'PYTHONPATH':
                for idx, val in enumerate(value.split(':')):
                    sys.path.insert(idx, val)
            os.environ[key] = value


def try_become_user(planb_user):
    """
    Become PlanB user if possible (if we're root). This way we won't
    create logs or other artifacts as root which won't be writable by
    the proper user later on.
    """
    import pwd

    if planb_user is not None:
        current_uid = os.getuid()
        planb_uid = pwd.getpwnam(planb_user)[2]

        if current_uid != planb_uid:
            if current_uid == 0:
                os.setgroups([])
                os.setuid(planb_uid)
            # else:
            #     import warnings
            #     warnings.warn(
            #         'cannot become planb user {!r}'.format(planb_user))


def get_version():
    try:
        version = metadata_version('planb')
    except PackageNotFoundError:
        version = '1.7'
    return 'PlanB {} (Django {})'.format(version, get_django_version())


def main():
    # Check env. If DJANGO_SETTINGS_MODULE is not set, we'll try to load
    # ${PLANB_ENVFILE}, /etc/planb/envvars or ./envvars to get USER,
    # PYTHONPATH and DJANGO_SETTINGS_MODULE.
    if 'DJANGO_SETTINGS_MODULE' not in os.environ:
        try_load_envvars((
            os.environ.get('PLANB_ENVFILE'),
            '/etc/planb/envvars',
            './envvars'))

    # Try becoming the PlanB user.
    try_become_user(os.environ.get('USER'))

    # Test reading the setting file.
    from importlib import import_module
    import_module(os.environ['DJANGO_SETTINGS_MODULE'])

    # Monkey patch get_version so it adds the planb version.
    from django.core.management import django, execute_from_command_line
    setattr(django, 'get_version', get_version)
    # Move to the django-admin wrapper.
    execute_from_command_line()


if __name__ == '__main__':
    main()
