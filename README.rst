PlanB
=====

TODO:

  * Add description/title after h1 heading.
  * Explain what this is (will be).
  * Add authors/copyright/dates from original project.


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

Installing requirements::

    cd /srv/planb
    pip3 install -r requirements.txt
    # (this should be superseded by setup.py-style config)




F.A.Q.
------

The ``mkvirtualenv`` said ``locale.Error: unsupported locale setting``.

    You need to install the right locales until ``perl -e setlocale`` is
    silent. How depends on your system and your config. See ``locale`` and
    e.g. ``locale-gen en_US.UTF-8``.
