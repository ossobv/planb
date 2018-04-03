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
    except:  # (AttributeError, CalledProcessError)
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
        if all(i.isdigit() for i in line.split('.')):
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

    version = (
        version_from_git() or
        version_from_changelog(changes))

    setup(
        name='planb',
        version=version,
        scripts=['scripts/planb'],
        data_files=[
            ('share/doc/planb', [
                'LICENSE', 'README.rst', 'CHANGES.rst']),
            ('share/planb', [
                'example_settings.py', 'wsgi.py',
                'rc.d/planb-queue.service'])],
        packages=find_packages() + [
            'planb.fixtures', 'planb.static', 'planb.templates'],
        package_data={
            'planb.fixtures': ['*.xml'],
            'planb.static': ['planb/js/jquery-postlink.js'],
            'planb.templates': [
                'admin/planb/hostconfig/change_form.html',
                'planb/report_email_body.txt']},
        description='PlanB automates remote SSH+rsync backups',
        long_description=('\n\n\n'.join([readme, changes])),
        author='Alex Boonstra, Walter Doekes, OSSO B.V.',
        author_email='wjdoekes+planb@osso.nl',
        url='https://github.com/ossobv/planb',
        license='GPLv3+',
        platforms=['linux'],
        classifiers=[
            'Development Status :: 4 - Beta',
            'Environment :: Web Environment',
            'Framework :: Django',
            'Framework :: Django :: 1.11',
            'Intended Audience :: System Administrators',
            ('License :: OSI Approved :: GNU General Public License v3 '
             'or later (GPLv3+)'),
            'Operating System :: POSIX :: Linux',
            'Programming Language :: Python',
            'Programming Language :: Python :: 3.4',
            'Programming Language :: Python :: 3.5',
            'Topic :: System :: Archiving :: Backup',
        ],
        install_requires=[
            'Django>=2.0,<2.1',
            'django-q>=0.9,<0.10',
            'django-multi-email-field>=0.4,<0.5',
            'dutree>=1.4',
            'mysqlclient>=1.3.7,<2',   # APT: python3-mysqldb
            'redis>=2.10.5,<3',        # APT: python3-redis
            'setproctitle>=1.1.8,<2',  # APT: python3-setproctitle
        ],
    )

# vim: set ts=8 sw=4 sts=4 et ai tw=79:
