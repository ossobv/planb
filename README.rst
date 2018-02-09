|PlanB|
=======

PlanB backs up your remote SSH-accessible files using rsync to a local ZFS
storage. Manage many hosts and host groups. Automate daily, weekly, monthly and
yearly backups with snapshots.


------------
How it looks
------------

At the moment, the interface is just a Django admin interface:

.. image:: assets/example_hosts.png
    :alt: A list of hosts configured in PlanB with most recent backup status

The files are stored on ZFS storage, using snapshots to keep earlier versions
of tiles. See this example shell transscript::

    # zfs list | grep mongo2
    rpool/BACKUP/experience-mongo2         9,34G  1,60T   855M  /srv/backups/experience-mongo2

    # ls -l /srv/backups/experience-mongo2/data/srv/mongodb
    total 646610
    -rw------- 1 planb nogroup   67108864 jun 17 17:03 experience.0
    -rw------- 1 planb nogroup  134217728 jun  9 16:01 experience.1
    ...

Those are the "current" files in the workspace. But you can go back in time::

    # zfs list -r -t all rpool/BACKUP/experience-mongo2 | head -n4
    NAME                                                  USED  AVAIL  REFER  MOUNTPOINT
    rpool/BACKUP/experience-mongo2                       9,34G  1,60T   855M  /srv/backups/experience-mongo2
    rpool/BACKUP/experience-mongo2@daily-201706031147        0      -   809M  -
    rpool/BACKUP/experience-mongo2@monthly-201706031147      0      -   809M  -

    # cd /srv/backups/experience-mongo2/.zfs/
    # ls -1
    daily-201706031147
    daily-201706031211
    daily-201706040001
    daily-201706050002
    ...

    # ls daily-201706031147/data/srv/mongodb -l
    total 581434
    -rw------- 1 planb nogroup   67108864 jun  2 18:21 experience.0
    -rw------- 1 planb nogroup  134217728 mei 29 14:38 experience.1
    ...


--------------------
Requirements / setup
--------------------

PlanB can be installed as a standalone Django_ application, or it can be
integrated in another Django project.

See `requirements.txt`_ or `setup.py`_ for up-to-date dependencies/requirements.

Basically, you'll need: ZFS storage, ssh and rsync, a webserver (nginx), python
hosting (uwsgi), a database (mysql), a communication/cache bus (redis) and a
few python packages.

For more detailed steps, see `Setting it all up`_ below.

.. _Django: https://www.djangoproject.com/
.. _`requirements.txt`: ./requirements.txt
.. _`setup.py`: ./setup.py


----
TODO
----

* Fix logrotate sample.
* Move broker from Redis to DjangoORM? We don't need redis for anything else..
* Add uwsgi-uid==djangoq-uid check?
* Re-add some form of "list-stale-mounts" (!).
  # contrib/list-stale-mounts | mail -E -s "[$HOSTNAME] Stale ZFS mounts?"
* Re-add non-INFO output from planb_custom.daily...
  # run_backupinfo | grep -vFB1 INFO/ /var/log/osso-backup/billing.log |
  # mail -E -s "[$HOSTNAME] Backup billing push"
* Re-add some kind of monthly report about what has been backupped.
  # parse_and_mail_backupdirs
* Add makefile for quick uninstall/install/uwsgi-reload?
* Sort HostGroups in HostConfig sidebar.
* Add pepcleaning pre-commit hook.
* Add flake-checking pre-commit hook.
* Add BCH checks.
* Alter HostGroup:
  - use fs-name and human-name
  - use asciifield for fs-name?
* Alter HostConfig:
  - use fs-name and optionally human-name
  - use asciifield for fs-name?
* Replace the exception mails for common errors (like failing rsync) to
  use mail_admins style mail.
* After using mail_admins style mail, we can start introducing mail digests
  instead: daily summary of backup successes and failures.
* Fix admin "Planb" name as "PlanB".
* Split off the subparts of the HostConfig to separate configs:
  - include-config
  - transport-config
  - retention-config
  - host-status (use this as main enqueue-view?)
* Use hostgroup+hostname in more places. Right now the friendly_name is
  too short. Also, use unique_together, so the friendlyname can be reused.
* BUG: Items added to /exclude list are not deleted from destination if
  they have already been backed up once.
* The 'data_files' in setup.py all get chucked into the virtualenv root.
  We should place most of them in share/planb/ instead.
* Replace the "daily report" hack with a signal-receiver.


-------
WARNING
-------

The Django-Q task scheduler is highly configurable from the
``/admin/``-view. With a little effort it will run user-supplied python
code directly. Any user with access to the schedulers will have
tremendous powers

**Recommendation**: don't give your users powers to edit the schedulers.
Use the fine-grained permissions of the Django-admin systems to limit
them to Hosts and HostGroups only.

*Perhaps we should disable web-access to it altogether.*


-----------------
Setting it all up
-----------------

If you follow the HOWTO below, you'll set up PlanB as a standalone
project. Those familiar with Django_ will know how to integrate it into
their own project.

The setup below assumes you'll be using the ``planb`` user. You're free
to change that consistently of course.


Setting up a ZFS pool
~~~~~~~~~~~~~~~~~~~~~

TODO: Document this briefly.


Setting up the project
~~~~~~~~~~~~~~~~~~~~~~

Setting up a virtualenv (optional)::

    mkdir -p /srv/virtualenvs
    echo 'WORKON_HOME=/srv/virtualenvs' >>~/.bashrc
    apt-get install python3-virtualenv python3-pip virtualenvwrapper
    # you may need to log in/out once after this

    mkvirtualenv planb --python=$(which python3) --system-site-packages

    mkdir /etc/planb
    cd /etc/planb
    pwd >$VIRTUAL_ENV/.project

    workon planb

Installing PlanB::

    apt-get install python3-mysqldb python3-redis python3-setproctitle
    pip install git+https://github.com/ossobv/planb.git@master

Setting up a local ``planb`` user::

    adduser planb --disabled-password --home=/var/spool/planb \
      --shell=/bin/bash --system

    sudo -H -u planb ssh-keygen -b 8192

.. note:: *You may want to back that ssh key up somewhere.*

Setting up the local environment::

    cat >/etc/planb/envvars <<EOF
    USER=planb
    PYTHONPATH=/etc/planb
    DJANGO_SETTINGS_MODULE=settings
    EOF

.. note:: *During development, you can use a local* ``./envvars`` *in your
           development directory or set* ``PLANB_ENVFILE`` *to a
           specific path. You can set* ``PYTHONPATH`` *to*
           ``/etc/planb:/home/yourname/src/planb`` *to develop on the
           production machine.*

Setting up the local configuration::

    cp /srv/virtualenvs/planb/example_settings.py /etc/planb/settings.py
    ${EDITOR:-vi} /etc/planb/settings.py

**Replace all *FIXME* entries in the ``settings.py``**

Make sure the SQL database exists. How to do that is beyond the scope of
this readme.

At this point, you should be able to run the ``planb`` script.

Set up the database and a web-user::

    planb migrate
    planb createsuperuser

Setting up uwsgi ``planb.ini``::

    [uwsgi]
    plugin = python3
    workers = 4

    chdir = /
    virtualenv = /srv/virtualenvs/planb
    wsgi-file = /srv/virtualenvs/planb/wsgi.py

    uid = planb
    gid = www-data
    chmod-socket = 660

    for-readline = /etc/planb/envvars
       env = %(_)
    endfor =

Set up static path, static files and log path::

    # see the STATIC_ROOT entry in your settings.py
    install -o planb -d /srv/http/YOURHOSTNAME/static

    planb collectstatic

    install -o planb -d /var/log/planb

Setting up nginx config::

    server {
        listen 80;
        server_name YOURHOSTNAME;

        root /srv/http/YOURHOSTNAME;

        location / {
            uwsgi_pass unix:/run/uwsgi/app/planb/socket;
            include uwsgi_params;
        }

        location /static/ {
        }
    }

Giving *PlanB* access to ZFS tools and paths::

    cat >/etc/sudoers.d/planb <<EOF
    planb ALL=NOPASSWD: /sbin/zfs, /bin/chown
    EOF

    zfs create rpool/BACKUP -o mountpoint=/srv/backups
    chown planb /srv/backups
    chmod 700 /srv/backups

Setting up ``qcluster`` for scheduled tasks::

    apt-get install redis-server

    # (in the source, this file is in rc.d)
    cp /srv/virtualenvs/planb/planb-queue.service /etc/systemd/system/
    ${EDITOR:-vi} /etc/systemd/system/planb-queue.service

    systemctl daemon-reload &&
      systemctl enable planb-queue &&
      systemctl start planb-queue &&
      systemctl status planb-queue

Installing automatic jobs::

    planb loaddata planb_jobs


-------------------------
Configuring a remote host
-------------------------

Create a ``remotebackup`` user on the remote host (or ``encbackup`` for
encrypted backups, which is beyond the scope of this document)::

    adduser --disabled-password remotebackup

Configure sudo access using ``visudo -f /etc/sudoers.d/remotebackup``::

    # Backup user needs to be able to get the files
    remotebackup ALL=NOPASSWD: /usr/bin/rsync --server --sender *
    remotebackup ALL=NOPASSWD: /usr/bin/ionice -c2 -n7 /usr/bin/rsync --server --sender *
    remotebackup ALL=NOPASSWD: /usr/bin/ionice -c3 /usr/bin/rsync --server --sender *

Observe how the ``--server --sender`` makes the rsync read-only.

Set up the ssh key like you'd normally do::

    mkdir -p ~remotebackup/.ssh
    cat >>~remotebackup/.ssh/authorized_keys <<EOF
    ... ssh public key from /var/spool/planb/.ssh/id_rsa.pub goes here ...
    EOF

    chmod 640 ~remotebackup/.ssh/authorized_keys
    chown remotebackup -R ~remotebackup/.ssh

When you use this pattern, you can tick ``use_sudo`` and set the remote
user to ``remotebackup``.


-------------------------------
Adding post-backup notification
-------------------------------

Do you want a notification when a backup succeeds? Or when it fails?

You can add something like this to your settings::

    from datetime import datetime
    from subprocess import check_call
    from django.dispatch import receiver
    from planb.signals import backup_done

    @receiver(backup_done)
    def notify_zabbix(sender, hostconfig, success, **kwargs):
        if success:
            key = 'planb.get_latest[{}]'.format(hostconfig.identifier)
            val = datetime.now().strftime('%s')
            cmd = (
                'zabbix_sender', '-c', '/etc/zabbix/zabbix_agentd.conf',
                '-k', key, '-o', val)
            check_call(cmd)

That combines nicely with a backup host discovery rule using ``blist``::

    # Machine discovery (redirects stderr to mail).
    UserParameter=planb.discovery, \
      ( planb blist --zabbix 3>&2 2>&1 1>&3 \
      | mail -E -s 'ERROR: planb.discovery (zabbix)' root ) 2>&1


----------------
Doing daily jobs
----------------

A quick hack to get daily reports up and running is by placing something
like this in ``/etc/planb/planb_custom.py``::

    from planb.contrib.billing import BossoBillingPoster, daily_hostgroup_report

    def daily_billing_report():
        """
        This function is added into: Home >> Task Queue >> Scheduled task
        As: "Report to Billing" <planb_custom.daily_bosso_report>
        """
        daily_hostgroup_report(BossoBillingPoster('http://my.url.here/'))


------
F.A.Q.
------

Can I use the software and customize it to my own needs?
    It is licensed under the GNU GPL version 3.0 or higher. See the LICENSE
    file for the full text. That means: probably yes, but you may be required to
    share any changes you make. But you were going to do that anyway, right?


The ``uwsgi`` log complains about *"No module named site"*.
    If your uwsgi fails to start, and the log looks like this::

        Python version: 2.7.12 (default, Nov 19 2016, 06:48:10)
        Set PythonHome to /srv/virtualenvs/planb
        ImportError: No module named site

    Then your uWSGI is missing the Python 3 module. Go install
    ``uwsgi-plugin-python3``.


The ``mkvirtualenv`` said ``locale.Error: unsupported locale setting``.
    You need to install the right locales until ``perl -e setlocale`` is
    silent. How depends on your system and your config. See ``locale`` and
    e.g. ``locale-gen en_US.UTF-8``.


Rsync complains about ``Invalid or incomplete multibyte or wide character``.
    If rsync returns with code 23 and says this::

        rsync: recv_generator: failed to stat "...\#351es-BCS 27-09-11.csv":
          Invalid or incomplete multibyte or wide character (84)

    Then you might be backing up old hosts with legacy Latin-1 encoding
    on the filesystem. Adding ``--iconv=utf8,latin1`` to the hostconfig
    flags should fix it.

    You may need rsync version 3 or higher for that.

    Right now we opt to *not* implement any of these workarounds:

    * Patch rsync to cope with ``EILSEQ`` (84) "Illegal byte sequence".
    * Cope with error code 23 and pretend that everything went fine.

    Instead, you should install a recent rsync and/or fix the filenames
    on your remote filesystem.


Rsync complains about ``failed to stat`` or ``mkdir failed``.
    If rsync returns these messages::

        rsync: recv_generator: failed to stat "...": Permission denied (13)
        rsync: recv_generator: mkdir "..." failed: Permission denied (13)

    Then you may be looking at parent directories with crooked
    permissions, like 077. Fix the permissions on the remote end.


Backup success mail are sent, but failure mails are not.
    Check the ``DEBUG`` setting. At the moment, error-mails are sent
    through the logging subsystem and that is disabled when running in
    debug-mode.


-------
Authors
-------

PlanB was started in 2013 as "OSSO backup" by Alex Boonstra at OSSO B.V. Since
then, it has been evolved into *PlanB*. When it was Open Sourced by Walter
Doekes in 2017, the old commits were dropped to ensure that any private compnay
information was not disclosed.


.. |PlanB| image:: assets/planb_head.png
    :alt: GoCollect
