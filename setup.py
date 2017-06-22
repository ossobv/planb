#!/usr/bin/env python
from distutils.core import setup
import sys

if sys.version_info < (3,):
    raise RuntimeError('PlanB is not built for Python older than 3')

if __name__ == '__main__':
    long_descriptions = []
    with open('README.rst') as file:
        long_descriptions.append(file.read())

    with open('CHANGES.rst') as file:
        long_descriptions.append(file.read())
        version = long_descriptions[-1].split(':', 1)[0].split('* ', 1)[1]
        assert version.startswith('v'), version
        version = version[1:]
        if not all(i.isdigit() for i in version.split('.')):
            version = '0_UNDEF'  # undefined version

    setup(
        name='planb',
        version=version,
        scripts=['bin/planb'],
        data_files=[('', [
            'LICENSE', 'README.rst', 'CHANGES.rst', 'wsgi.py',
            'rc.d/planb-queue.service'])],
        packages=[
            'planb',
            'planb.fixtures',
            'planb.static',
            'planb.templates'],
        package_data={
            'planb.fixtures': ['*.xml'],
            'planb.static': ['planb/js/jquery-postlink.js'],
            'planb.templates': ['admin/planb/hostconfig/change_form.html']},
        description='PlanB automates remote SSH+rsync backups',
        long_description=('\n\n\n'.join(long_descriptions)),
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
            'Django>=1.11.1,<1.12',
            'django-q>=0.8.0,<0.9',
            'django-multi-email-field>=0.4,<0.5',
            'mysqlclient>=1.3.7,<2',   # APT: python3-mysqldb
            'redis>=2.10.5,<3',        # APT: python3-redis
            'setproctitle>=1.1.8,<2',  # APT: python3-setproctitle
        ],
    )

# vim: set ts=8 sw=4 sts=4 et ai tw=79:
