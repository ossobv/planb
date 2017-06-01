PlanB
=====

TODO:

* Add description/title after h1 heading in docs.
* Explain what this is (will be).
* Add authors/copyright/dates from original project.
* Add pepcleaning pre-commit hook.
* Add flake-checking pre-commit hook.
* Add BCH checks.
* Use proper setup.py-setup and easier settings.py,
  possibly with uwsgi.ini-only environment?
* Alter HostGroup:
  - use fs-name and human-name
  - use asciifield for fs-name?
* Alter HostConfig:
  - use fs-name and optionally human-name
  - use asciifield for fs-name?
* Check whether the mount-point != zpool-name works properly.
* Fix System calls to always save stderr for exception output.
* Check the 'bclone' call: it reports 2 warnings which should be fixed.
* Fix admin "Planb" name as "PlanB".
* Fix "iconv=utf8,latin1" on ancient hosts? Doesn't exist there..
  Fallback to without --iconv and accept code 23 as non-failed?
* Split off the subparts of the HostConfig to separate configs:
  - include-config
  - transport-config
  - retention-config
  - host-status (use this as main enqueue-view?)
* Use hostgroup+hostname in more places. Right now the friendly_name is
  too short. Also, use unique_together, so the friendlyname can be reused.
* Don't allow enqueue-ing of enabled=False hosts!
* Add mysql-backup to backup this SQL.
* Document how to use the remotebackup user with ionice and sudo.


-------
WARNING
-------

The Django-Q task scheduler is highly configurable from the
/admin/-view. With a little effort it will run user-supplied python code
directly. Any user with access to the schedulers will have tremendous
powers

**Recommendation**: don't give your users powers to edit the schedulers.
Perhaps we should disable web-access to it altogether.


-----------------
Setting it all up
-----------------

TODO:

* Explain how you can skip some or all parts here.
* Move the optional details, like how to set up a database or ZFS, to a
  separate heading at the bottom.


Setting up a ZFS pool
~~~~~~~~~~~~~~~~~~~~~

TODO: Document this briefly.


Setting up a database
~~~~~~~~~~~~~~~~~~~~~

Something like this::

    apt-get install mariadb-server  # or mysql-server, or postgres, or ...

TODO: Explain that we need a user, a database, a sane collation.


Setting up the project
~~~~~~~~~~~~~~~~~~~~~~

Cloning project::

    git clone https://github.com/ossobv/planb.git /srv/planb

Setting up environment/virtualenv::

    mkdir -p /srv/venv
    echo 'WORKON_HOME=/srv/venv' >>~/.bashrc
    apt-get install python3-virtualenv python3-pip virtualenvwrapper
    # you may need to log in/out once after this

    mkvirtualenv planb --python=$(which python3)

    cd /srv/planb
    pwd >$VIRTUAL_ENV/.project

Installing requirements::

    workon planb
    pip3 install -r requirements.txt
    # (this should be superseded by setup.py-style config)

Setting up the database and a PlanB user::

    ./manage migrate
    ./manage createsuperuser

Setting up a local user::

    adduser planb --disabled-password --home=/var/spool/planb \
      --shell=/bin/bash --system

    sudo -H -u planb ssh-keygen -b 8192

You may want to back that ssh key up somewhere.

Setting up uwsgi ``planb.ini``::

    [uwsgi]
    plugin = python3
    workers = 4

    chdir = /srv/planb
    wsgi-file = /srv/planb/wsgi.py
    virtualenv = /srv/venv/planb

    env = DJANGO_SETTINGS_MODULE=settings

    uid = planb
    gid = www-data
    chmod-socket = 660

Set up static path::

    mkdir -p /srv/http/planb.example.com/static
    ./manage collectstatic

Set up log file path::

    mkdir /var/log/planb
    chown planb /var/log/planb

Setting up nginx config::

    server {
        listen 80;
        server_name planb.example.com;

        root /srv/http/planb.example.com;

        location / {
            uwsgi_pass unix:/run/uwsgi/app/planb/socket;
            include uwsgi_params;
        }

        location /static/ {
        }
    }

Setting up ZFS::

    cat >/etc/sudoers.d/planb <<EOF
    planb ALL=NOPASSWD: /sbin/zfs, /bin/chown
    EOF

    zfs create rpool/BACKUP -o mountpoint=/srv/backups
    chown planb /srv/backups
    chmod 700 /srv/backups

Setting up qcluster::

    apt-get install redis-server
    cp rc.d/planb-queue.service /etc/systemd/system/ &&
      systemctl enable planb-queue &&
      systemctl start planb-queue &&
      systemctl status planb-queue

Installing automatic jobs::

    ./manage loaddata planb_jobs



------
F.A.Q.
------

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
