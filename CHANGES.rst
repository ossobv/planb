Changes
-------

v1.3 - *2018-03-19*
~~~~~~~~~~~~~~~~~~~

**Web interface**

- Disallow deletion of non-empty host groups.

**CLI**

- Add ``breport`` command to send out backup reports. See the template
  in templates/planb/report_email_body.txt. Note that the report is
  still in alpha stage. NOTE: To get e-mail reports as well, you need
  to have ``rst2html`` installed.
- Add ``--with-disabled`` to ``confexport`` command to get complete
  exports.
- Fix that planb runserver can be used for development (through
  PYTHONPATH propagation).

**Other**

- Dependency updates to Django 2.0+.
- Add backup history record keeping, for better logging and averages.


v1.2 - *2017-09-18*
~~~~~~~~~~~~~~~~~~~

- Fix release, this time without pyc files and with wheel package.
  Run this for upload: python setup.py sdist bdist_wheel upload


v1.1 - *2017-09-18*
~~~~~~~~~~~~~~~~~~~

**Settings**

- Add ``PLANB_DEFAULT_INCLUDES``.
- Rename ``ZFS_BIN``, ``SUDO_BIN`` and ``RSYNC_BIN`` to ``PLANB_<setting>``.
- Fix allowing use of alternate ``DJANGO_SETTINGS_MODULE``.

**Web interface**

- Add hosts to hostgroup listing.
- Allow ordering hosts by enabled/queued/running.

**CLI**

- Add "stale mounts" listing (planb slist).
- Create "hostconfig" export in YAML or JSON format (planb confexport).

**Queue**

- Fix so long running jobs don't suffer from lost DB connections.

**Other**

- Misc refactoring/cleanup.


v1.0 - *2017-07-11*
~~~~~~~~~~~~~~~~~~~

- Initial release.
