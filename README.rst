|PlanB|
=======

PlanB backs up your remote files to a local ZFS storage. Manage many
hosts and host groups. Automate hourly, daily, weekly, monthly and
yearly backups with snapshots.

The following data transfer methods are supported:

* ssh+rsync (built-in);
* ssh+rsync of Kubernetes volume mounts (through `kubersync
  <./contrib/kubersync.sh>`_), like Rook managed Ceph;
* snapshots of ZFS (encrypted) datasets (through `planb-zfssync
  <./contrib/planb-zfssync.sh>`_);
* snapshots of ZFS volumes (through `planb-zfssync
  <./contrib/planb-zfssync.sh>`_);
* copies of (large) *OpenStack Swift* containers (through `planb-swiftsync
  <./contrib/planb-swiftsync.py>`_);
* custom transfer (through your own custom ``transfer_exec`` script).


------------------
What it looks like
------------------

At the moment, the interface is just a *Django* admin interface:

.. image:: assets/example_hosts.png
    :alt: A list of hosts configured in PlanB with most recent backup status

The files are stored on *ZFS* storage. It uses *ZFS* snapshots to keep earlier
versions of files. See this example shell transscript::

    # zfs list | grep mongo2
    tank/BACKUP/experience-mongo2   9,34G  1,60T   855M  /srv/backups/experience-mongo2

    # ls -l /srv/backups/experience-mongo2/data/srv/mongodb
    total 646610
    -rw------- 1 planb nogroup   67108864 jun 17 17:03 experience.0
    -rw------- 1 planb nogroup  134217728 jun  9 16:01 experience.1
    ...

Those are the "current" files in the workspace. But you can go back in time::

    # zfs list -r -t all tank/BACKUP/experience-mongo2 | head -n4
    NAME                                                 USED  AVAIL  REFER  MOUNTPOINT
    tank/BACKUP/experience-mongo2                       9,34G  1,60T   855M  /srv/backups/experience-mongo2
    tank/BACKUP/experience-mongo2@planb-20170603T1147Z      0      -   809M  -
    tank/BACKUP/experience-mongo2@planb-20170603T1211Z      0      -   809M  -

    # cd /srv/backups/experience-mongo2/.zfs/
    # ls -1
    planb-20170603T1147Z
    planb-20170603T1211Z
    planb-20170604T0001Z
    planb-20170605T0002Z
    ...

    # ls planb-20170603T1147Z/data/srv/mongodb -l
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

Basically, you'll need: *ZFS* storage, ssh and rsync, a webserver
(nginx), python hosting (uwsgi), a database (mysql), a
communication/cache bus (redis) and a few python packages.

For more detailed steps, see `Setting it all up`_ below.

.. _Django: https://www.djangoproject.com/
.. _`requirements.txt`: ./requirements.txt
.. _`setup.py`: ./setup.py


----
TODO
----

* Encryption: right now, encryption keys are still a bit of a mess:

  - stuff is stored in tank/_local; should use some kind of vault;
  - when removing/renaming, those keys are not updated alongside;
  - planb-zfssync.sh does not clean up snapshots created before
    send/recv failure (e.g. because remote did not support --raw)
  - add key rotation example scripts?

* Docs: add documentation for sync from previous unencrypted filesets?
* Docs: add a bit of documentation on how to work with encrypted filesets
* Consider: move the hostgroup contents to separate filesets, so as to
  create a more readable fileset listing. tank/HOSTGROUP/FILESET instead
  of tank/HOSTGROUP-FILESET.
* RFE: Add post-backup.d directory somewhere where we can place
  post-backup-done scripts to manually do X or Y.
* RFE: Add planb group for better permission management.
* RFE: Also store user/group permissions on/after rsync (using xattr
  extended attributes?).
* BUG: Items added to /exclude list are not deleted from destination if
  they have already been backed up once. The rsync job would need some
  way to keep track of changes in include/exclude settings, and run a
  cleanup in case they are changed. (See metadata storage like
  planb-swiftsync.* files.)
* RFE: Standardize stdout/stderr output from Rsync/Exec success (and
  prepend "> " to output) to be more in line with failure.
* RFE: Add possibility to feed back snapshot size from the individual
  Transport instead of using dutree. Parsing the swiftsync listings is
  fast after all.
* FIX: Add uwsgi-uid==djangoq-uid check?
* Replace the exception mails for common errors (like failing rsync) to
  use mail_admins style mail.
* After using mail_admins style mail, we can start introducing mail digests
  instead: daily summary of backup successes and failures.
* Replace the "daily report" hack with a signal-receiver.
* Clarify why there's a /contrib/ and a /planb/contrib/ directory.


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

You should really do your own research on this. If you're lucky, your
operating system has native support for *ZFS*, and then this is
relatively easy.

Please read `README-zpool.rst <./README-zpool.rst>`_ for a quick
introduction. When you're done, things should look somewhat like this:

.. code-block:: console

    # zpool status
      pool: tank
     state: ONLINE
      scan: none requested
    config:

      NAME                                  STATE
      tank                                  ONLINE
        raidz2-0                            ONLINE
          scsi-SSEAGATE_ST10000NM0226_6351  ONLINE
          scsi-SSEAGATE_ST10000NM0226_0226  ONLINE
          scsi-SSEAGATE_ST10000NM0226_8412  ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
        raidz2-1                            ONLINE
          scsi-SSEAGATE_ST10000NM0226_0123  ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
      spares
        scsi-SSEAGATE_ST10000NM0226_9866    AVAIL
        scsi-SSEAGATE_ST10000NM0226_5992    AVAIL


Setting up the project
~~~~~~~~~~~~~~~~~~~~~~

*This section assumes you know a little about Python, pip and virtual
envs. Details may vary a slight bit across distro versions.*

Set up a virtualenv (optional)::

    mkdir -p /srv/virtualenvs
    echo 'WORKON_HOME=/srv/virtualenvs' >>~/.bashrc
    apt-get install python3-virtualenv python3-pip virtualenvwrapper
    # you may need to log in/out once after this

    # you may need /usr/share/bash-completion/completions/virtualenvwrapper
    # sources in your bashrc
    mkvirtualenv planb --python=$(which python3) --system-site-packages
    workon planb

    mkdir /etc/planb
    cd /etc/planb
    pwd >$VIRTUAL_ENV/.project  # or the src dir, if you're going to edit a lot

Install PlanB prerequisites::

    apt-get install redis-server  # and: mysql-server or postgresql

Install PlanB dependencies through apt (optional)::

    apt-get install python3-redis python3-setproctitle
    # .. and: python3-mysqldb or python3-psycopg2

Install PlanB (including depedencies) from PyPI::

    pip3 install planb

Install PlanB (including dependencies) from git::

    pip3 install git+https://github.com/ossobv/planb.git@master#egg=planb

Set up a local ``planb`` user::

    adduser planb --disabled-password --home=/var/spool/planb \
      --shell=/bin/bash --system

    sudo -H -u planb ssh-keygen -t ed25519      # use elliptic curve
    sudo -H -u planb ssh-keygen -t rsa -b 8192  # or use RSA if you're old

.. note:: *You may want to back that ssh key up somewhere.*

Set up the local environment::

    cat >/etc/planb/envvars <<EOF
    USER=planb
    PYTHONPATH=/etc/planb
    DJANGO_SETTINGS_MODULE=settings
    EOF

.. note:: *PlanB looks for an environment file in the locations:*
          - env PLANB_ENVFILE
          - /etc/planb/envvars
          - ./envvars
          *The first file that can be loaded will be used.*

Set up the local configuration::

    cp ${VIRTUAL_ENV:-/usr/local}/share/planb/example_settings.py \
      /etc/planb/settings.py
    ${EDITOR:-vi} /etc/planb/settings.py

**Replace all *FIXME* entries in the ``settings.py``**

.. note:: *For development you only need the settings module which can
           be placed in the project root.*
           ``cp -n example_settings.py settings.py``
           *You can use* ``python setup.py develop`` *to install planb
           in develop mode. This links the source directory to python
           site-packages and is especially useful for production hacking.*

Make sure the SQL database exists. How to do that is beyond the scope of
this readme.

At this point, you should be able to run the ``planb`` script.

Set up the database and a web-user::

    planb migrate
    planb createsuperuser

Set up uwsgi ``planb.ini``::

    [uwsgi]
    plugin = python3
    workers = 4

    chdir = /
    virtualenv = /srv/virtualenvs/planb
    wsgi-file = /srv/virtualenvs/planb/share/planb/wsgi.py

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

Set up nginx config::

    server {
        listen 80;
        server_name YOURHOSTNAME;

        root /srv/http/YOURHOSTNAME;

        location / {
            uwsgi_pass unix:/run/uwsgi/app/planb/socket;
            include uwsgi_params;
        }
        location = /favicon.ico {
            return 404;
        }
        location /static/ {
        }
    }

Give *PlanB* *sudo* access to *ZFS* tools and fix paths::

    cat >/etc/sudoers.d/planb <<EOF
    planb ALL=NOPASSWD: /sbin/zfs, /bin/chown
    EOF

    zfs create tank/BACKUP -o mountpoint=/srv/backups
    chown planb /srv/backups
    chmod 700 /srv/backups

(Note that setting up a different mount point is optional. See also
`README-zpool.rst <./README-zpool.rst>`_ for additional tips.

Set up ``qcluster`` for scheduled tasks::

    # (in the source, this file is in rc.d)
    cp ${VIRTUAL_ENV:-/usr/local}/share/planb/planb-queue.service \
      /etc/systemd/system/

    ${EDITOR:-vi} /etc/systemd/system/planb-queue.service

    systemctl daemon-reload &&
      systemctl enable planb-queue &&
      systemctl start planb-queue &&
      systemctl status planb-queue

Set up the ``qcluster`` for dutree tasks. If you do not use dutree
or if you want to run dutree on the default qcluster you can set
``Q_DUTREE_QUEUE='PlanB'`` in ``/etc/planb/settings.py``.::

    cp ${VIRTUAL_ENV:-/usr/local}/share/planb/planb-queue-dutree.service \
      /etc/systemd/system/

    ${EDITOR:-vi} /etc/systemd/system/planb-queue-dutree.service

    systemctl daemon-reload &&
      systemctl enable planb-queue-dutree &&
      systemctl start planb-queue-dutree &&
      systemctl status planb-queue-dutree

Install automatic jobs::

    planb loaddata planb_jobs

Don't forget a logrotate config::

    cat >/etc/logrotate.d/planb <<EOF
    /var/log/planb/*.log {
            weekly
            missingok
            rotate 52
            compress
            delaycompress
            notifempty
            create 0644 planb www-data
            sharedscripts
    }
    EOF

Create aliases to quickly mount/unmount the current working directory
in your ``~/.bashrc``::

    alias zfs-quick-mount="zfs load-key -L \
        "'"file:///tank/_local/zfskeys/${PWD#/}/_key.bin" "${PWD#/}" &&
        zfs mount "${PWD#/}" && cd .'
    alias zfs-quick-umount='cd / && if zfs umount "${OLDPWD#/}"
        then zfs unload-key "${OLDPWD#/}"; cd "${OLDPWD}"
        else cd "${OLDPWD}"; false; fi'

.. warning:: WARNING: The example above uses local key files! This will be
             fixed/replaced in upcoming commits.


-------------------------
Configuring a remote host
-------------------------

Create a ``remotebackup`` user on the remote host (or ``encbackup`` for
backups encrypted at the source [#]_ [#]_, which is beyond the scope of
this document)::

    useradd -m remotebackup

Configure *sudo* access using ``visudo -f /etc/sudoers.d/remotebackup``::

    # Backup user needs to be able to get the files
    remotebackup ALL=NOPASSWD: /usr/bin/rsync --server --sender *
    remotebackup ALL=NOPASSWD: /usr/bin/ionice -c2 -n7 /usr/bin/rsync --server --sender *
    remotebackup ALL=NOPASSWD: /usr/bin/ionice -c3 /usr/bin/rsync --server --sender *

    # Optional, for planb-zfsync.sh (only destroy snapshots with @ in the name)
    remotebackup ALL=NOPASSWD: /sbin/zfs destroy *@*
    remotebackup ALL=NOPASSWD: /sbin/zfs list *
    remotebackup ALL=NOPASSWD: /sbin/zfs send *
    remotebackup ALL=NOPASSWD: /sbin/zfs set *
    remotebackup ALL=NOPASSWD: /sbin/zfs snapshot *

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
    def notify_zabbix(sender, fileset, success, **kwargs):
        if success:
            key = 'planb.get_latest[{}]'.format(fileset.unique_name)
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

A quick hack to get daily reports up and running, is by placing something
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
    It is licensed under the GNU GPL version 3.0 or higher. See the
    LICENSE file for the full text. That means: probably yes, but you
    may be required to share any changes you make. But you were going to
    do that anyway, right?


Mails for backup success are sent, but mails for failure are not.
    Check the ``DEBUG`` setting. At the moment, error-mails are sent
    through the logging subsystem and that is disabled when running in
    debug-mode.


Where are the ssh host fingerprints (``known_hosts`` files) stored?
    They're in ``~planb/.ssh/known_hosts.d/``. If you want to ``ssh``
    manually, you can add this to ``~planb/.profile``::

        ssh() {
            for arg in "$@"; do
                case $arg in
                -*) ;;
                *) break ;;
                esac
            done
            if test -n "$arg"; then
                host=${arg##*@}
                echo "(adding: \
        -o UserKnownHostsFile=$HOME/.ssh/known_hosts.d/$host)" >&2
                /usr/bin/ssh -o HashKnownHosts=no \
                  -o UserKnownHostsFile=$HOME/.ssh/known_hosts.d/$host "$@"
            else
                /usr/bin/ssh "$@"
            fi
        }


Can I use a *jump host*?
    You can add ``-e 'ssh -J jumpuser@jumphost'`` to the *rsync*
    transport flags. Observe that the known hosts file of *target* will
    contain the fingerprint of the *jump host*.


Are bandwidth limits in place?
    Yes, the default for the *rsync* transport is 10MB/s (megabyte). You
    can lower or raise this by adding ``--bwlimit=10M`` to the transport
    flags.


I've increased the bwlimit, but it's still slow.
    If you notice that you're limited by ssh encryption CPU speed, you
    can consider setting the preferred ciphers in ``~planb/.ssh/config``::

        Host *
            # The default is:
            #
            #   chacha20-poly1305@openssh.com,
            #   aes128-ctr,aes192-ctr,aes256-ctr,
            #   aes128-gcm@openssh.com,aes256-gcm@openssh.com
            #
            # The available ciphers may be obtained using "ssh -Q cipher".
            # (Adding a non-existent one will yield a "Bad SSH2 cipher spec".)
            #
            # The AES ciphers are commonly hardware/CPU accelerated.
            #
            Ciphers aes128-ctr,aes128-gcm@openssh.com,aes256-ctr,\
                aes256-gcm@openssh.com,chacha20-poly1305@openssh.com,3des-cbc

Removing a fileset does not wipe the filesystem from disk, what should I do?
    This is done intentionally. You should periodically use ``planb slist
    --stale`` to check for *stale* filesystems.

    You can them remove them manually using ``zfs destroy [-r] FILESYSTEM``.


Rsync complains about ``failed to stat`` or ``mkdir failed``.
    If rsync returns these messages::

        rsync: recv_generator: failed to stat "...": Permission denied (13)
        rsync: recv_generator: mkdir "..." failed: Permission denied (13)

    Then you may be looking at parent directories with crooked
    permissions, like 077. Fix the permissions on the remote end.

    However, many of these problems have likely been fixed by the
    addition of the ``--chmod=Du+rwx`` rsync option.


Rsync complains about ``Invalid or incomplete multibyte or wide character``.
    If rsync returns with code 23 and says this::

        rsync: recv_generator: failed to stat "...\#351es-BCS 27-09-11.csv":
          Invalid or incomplete multibyte or wide character (84)

    Then you might be backing up old hosts with legacy Latin-1 encoding
    on the filesystem. Adding ``--iconv=utf8,latin1`` to the rsync transport
    flags should fix it.

    You may need rsync version 3 or higher for that.

    Right now we opt to *not* implement any of these workarounds:

    * Patch rsync to cope with ``EILSEQ`` (84) "Illegal byte sequence".
    * Cope with error code 23 and pretend that everything went fine.

    Instead, you should install a recent rsync and/or fix the filenames
    on your remote filesystem.


The ``mkvirtualenv`` said ``locale.Error: unsupported locale setting``.
    You need to install the right locales until ``perl -e setlocale`` is
    silent. How depends on your system and your config. See ``locale`` and
    e.g. ``locale-gen en_US.UTF-8``.


The ``uwsgi`` log complains about *"No module named site"*.
    If your uwsgi fails to start, and the log looks like this::

        Python version: 2.7.12 (default, Nov 19 2016, 06:48:10)
        Set PythonHome to /srv/virtualenvs/planb
        ImportError: No module named site

    Then your uWSGI is missing the Python 3 module. Go install
    ``uwsgi-plugin-python3``.


-------
Authors
-------

PlanB was started in 2013 as "OSSO backup" by Alex Boonstra at OSSO B.V. Since
then, it has been evolved into *PlanB*. When it was Open Sourced by Walter
Doekes in 2017, the old commits were dropped to ensure that any private company
information was not disclosed. Since then, Harm Geerts has also been
busy on the project.


---------
Footnotes
---------

.. [#] If you want your data encrypted before it gets sent to the PlanB server,
       check out the OSSO blog:
       `on the fly encrypted backups using gocryptfs (2020)
       <https://www.osso.nl/blog/offsite-on-the-fly-encrypted-backups-gocryptfs/>`_
.. [#] An older OSSO blog about on the fly encryption at the source:
       `on the fly encrypted backups using encfs (2015)
       <https://www.osso.nl/blog/on-the-fly-encrypted-backups/>`_

.. |PlanB| image:: assets/planb_head.png
    :alt: PlanB - automating remote backups and snapshots with zfs/rsync

