PlanB
=====

TODO:

* Add description/title after h1 heading.
* Explain what this is (will be).
* Add authors/copyright/dates from original project.
* Add pepcleaning pre-commit hook.
* Add flake-checking pre-commt hook.
* Add BCH checks.
* Alter HostGroup:
  - use fs-name and human-name
  - use asciifield for fs-name?
* Alter HostConfig:
  - use fs-name and optionally human-name
  - use asciifield for fs-name?
* Fix /home/backup => $HOME
* Check whether the mount-point != zpool-name works properly.
* Fix 'sudo' usage for all ZFS calls!
* Fix admin "Planb" name as "PlanB".


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



------
F.A.Q.
------

The ``mkvirtualenv`` said ``locale.Error: unsupported locale setting``.
    You need to install the right locales until ``perl -e setlocale`` is
    silent. How depends on your system and your config. See ``locale`` and
    e.g. ``locale-gen en_US.UTF-8``.
