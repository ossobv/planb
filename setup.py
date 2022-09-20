#!/usr/bin/env python
import os.path
import sys
from distutils.core import setup
from setuptools import find_packages

try:
    from subprocess import check_output
except ImportError:
    check_output = None

if sys.version_info < (3,):
    raise RuntimeError('PlanB is not built for Python older than 3')


def version_from_git():
    try:
        version = check_output(
            "test -d .git && git fetch --tags && "
            "git describe --tags --dirty | "
            "sed -e 's/-/+/;s/[^A-Za-z0-9.+]/./g'",
            shell=True)
    except Exception:  # (AttributeError, CalledProcessError)
        return None

    version = version.decode('ascii', 'replace')
    if not version.startswith('v'):
        return None

    return version[1:].rstrip()


def version_from_changelog(changelog):
    versions = changelog.split('\nv')[1:]
    incomplete = False

    for line in versions:
        assert line and line[0].isdigit(), line
        line = line.split(' ', 1)[0]
        if all(i.isdigit() or i.startswith(('dev', 'post', 'rc'))
               for i in line.split('.')):
            version = line  # last "complete version"
            break
        incomplete = True
    else:
        return '0+1.or.more'  # undefined version

    if incomplete:
        version += '+1.or.more'
    return version


if __name__ == '__main__':
    here = os.path.dirname(__file__)
    os.chdir(here or '.')

    with open('README.rst') as fp:
        readme = fp.read()
    with open('CHANGES.rst') as fp:
        changes = fp.read()

    # TODO: perhaps check out https://github.com/pypa/setuptools_scm
    version = (
        version_from_git()
        or version_from_changelog(changes))

    setup(
        name='planb',
        version=version,
        entry_points={
            'console_scripts': ['planb=planb.__main__:main'],
        },
        data_files=[
            ('share/doc/planb', [
                'LICENSE', 'README.rst', 'CHANGES.rst']),
            ('share/planb', [
                'example_settings.py', 'wsgi.py',
                'rc.d/planb-queue.service',
                'rc.d/planb-queue-dutree.service'])],
        packages=find_packages() + [
            'planb.fixtures', 'planb.static', 'planb.templates'],
        include_package_data=True,  # see MANIFEST.in
        description='PlanB automates remote SSH+rsync backups',
        long_description=('\n\n\n'.join([readme, changes])),
        author='Alex Boonstra, Harm Geerts, Walter Doekes, OSSO B.V.',
        author_email='wjdoekes+planb@osso.nl',
        url='https://github.com/ossobv/planb',
        license='GPLv3+',
        platforms=['linux'],
        classifiers=[
            'Development Status :: 4 - Beta',
            'Environment :: Web Environment',
            'Framework :: Django',
            'Framework :: Django :: 2',
            'Framework :: Django :: 3',
            'Intended Audience :: System Administrators',
            ('License :: OSI Approved :: GNU General Public License v3 '
             'or later (GPLv3+)'),
            'Operating System :: POSIX :: Linux',
            'Programming Language :: Python',
            'Programming Language :: Python :: 3',
            'Topic :: System :: Archiving :: Backup',
        ],
        # Keep install_requires in sync with requirements.txt and
        # constraints.txt
        install_requires=[
            'Django>=2.2,<3.3',
            'django-q>=1.2.1,<2',
            'django-multi-email-field>=0.6.1,<0.7',
            'dutree>=1.6,<2',
            'PyYAML>=5.1.1',
            # Allow any redis version that is compatible with django-q.
            'redis',                   # APT: python3-redis
            'setproctitle>=1.1.8,<2',  # APT: python3-setproctitle
            'python-dateutil>=2.8.1,<3',
        ],
    )

# vim: set ts=8 sw=4 sts=4 et ai tw=79:
