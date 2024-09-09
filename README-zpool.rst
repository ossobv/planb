Setting up ZFS for PlanB
========================

**This is not a full-blown ZFS setup guide.** But it will provide some tips
to get a *zpool* up and running for *PlanB*.

*Here, the most common setup using raidz2 is described with a concise
explanation of the parameters. For more information, tips and tweaks,
and why you should not skimp on non-ECC memory, the author refers you to
The Internet™*.

This how-to assumes you're using ZFS 0.8.x on Linux, but it will likely
work on other versions with slight adaptations.

1. `Selecting/preparing disks`_
2. `Using native ZFS encryption`_
3. `Setting up the zpool`_
4. `Explanation of zpool attributes`_
5. `Replacing faulty disks`_


-------------------------
Selecting/preparing disks
-------------------------

So, start with a bunch of disks. Let's say 34 10TB disks:

.. code-block:: console

    # cat /proc/partitions  | grep ' sd[a-z]*$' | sort -Vk4
       8        0 9766436864 sda
      65      160 9766436864 sdaa
      65      176 9766436864 sdab
    ...
      65      128 9766436864 sdy
      65      144 9766436864 sdz

The disks don't *need* to have the same size, but it helps (and hot
spares will need to match the largest, for obvious reasons). For the common
setup, you'll use the entire disk and not a partition. (*ZFS* will do
its own partitioning, but you don't need to worry about that.)

You will want to *triple check* which disks you're using. You don't want
to overwrite your operating system (OS) or some other important data.
(In my case, the OS is on separate *nvme* drives, so I can safely use all
*sdX* drives.)

These 34 disks will go into three *ZFS vdevs* (see `vdevs and raidz2`_ below):

1. 10 disks
2. 10 disks
3. 10 disks
4. 4 hot spares

**A pro tip** here is to use the device identifiers instead of the kernel
generated names. I don't think ZFS will have a problem finding the right
device if the kernel renames *sda* to *sdb*, but when you're swapping
defective disks, you'll be happy when you can match
*scsi-SSEAGATE_ST10000NM0226_0123* to the identifier printed on the
physical disk.

So, step 1, find the drives:

.. code-block:: console

    # ls -go /dev/disk/by-id/ | grep '/sda$'
    lrwxrwxrwx 1 10 Jun 24 08:29 scsi-35000c500af2fd4df -> ../../sda
    lrwxrwxrwx 1 10 Jun 24 08:29 scsi-SSEAGATE_ST10000NM0226_0123 -> ../../sda
    lrwxrwxrwx 1 10 Jun 24 08:29 wwn-0x5000c500af2fd4df -> ../../sda

    # ls -go /dev/disk/by-id/ | grep 'scsi-[^ ]*_.*/sd[a-z]*$'
    lrwxrwxrwx 1  9 Jun 24 08:29 scsi-SSEAGATE_ST10000NM0226_0101 -> ../../sdac
    lrwxrwxrwx 1 10 Jun 24 08:29 scsi-SSEAGATE_ST10000NM0226_0123 -> ../../sda
    lrwxrwxrwx 1 10 Jun 24 08:29 scsi-SSEAGATE_ST10000NM0226_0226 -> ../../sde
    ...

    # ls -go /dev/disk/by-id/ | grep 'scsi-[^ ]*_.*/sd[a-z]*$' | wc -l
    34

Drop them in a file somewhere:

.. code-block:: console

    # ls -go /dev/disk/by-id/ | grep 'scsi-[^ ]*_.*/sd[a-z]*$' |
        awk '{print $7}'
    scsi-SSEAGATE_ST10000NM0226_0101
    scsi-SSEAGATE_ST10000NM0226_0123
    scsi-SSEAGATE_ST10000NM0226_5148
    ...

    # ls -go /dev/disk/by-id/ | grep 'scsi-[^ ]*_.*/sd[a-z]*$' |
        awk '{print $7}' >disks

However, *now they are sorted by serial number*. I don't know if the
serials are generated incrementally, but if they are, those with similar
numbers *may be part of a bad batch*. **We don't want all bad disks to
end up on the same vdev. If a vdev fails, all data is lost.**

So, to counter that, a simple ``shuf`` (shuffle) of the data is
sufficient to ease my paranoia.

.. code-block:: console

    # ls -go /dev/disk/by-id/ | grep 'scsi-[^ ]*_.*/sd[a-z]*$' |
        awk '{print $7}' | shuf >disks

Okay, now that the disks are shuffled. Open an editor on the created
``disks`` file and prepend numbers.
``0`` (and a space) before the 10 first disks, ``1`` before the next 10,
then ``2`` and lastly ``S`` for the spares. Your file now looks like
this::

    0 scsi-SSEAGATE_ST10000NM0226_6351
    0 scsi-SSEAGATE_ST10000NM0226_0226
    0 scsi-SSEAGATE_ST10000NM0226_8412
    ...
    1 scsi-SSEAGATE_ST10000NM0226_0123
    ...
    S scsi-SSEAGATE_ST10000NM0226_8412

That's nice, because now we can quickly get the chosen disks from that file.
For example, find ``S `` to get the 4 spares:

.. code-block:: console

    # awk '/^S /{print "disk/by-id/" $2}' disks
    disk/by-id/scsi-SSEAGATE_ST10000NM0226_9866
    disk/by-id/scsi-SSEAGATE_ST10000NM0226_5992
    disk/by-id/scsi-SSEAGATE_ST10000NM0226_5900
    disk/by-id/scsi-SSEAGATE_ST10000NM0226_8412


---------------------------
Using native ZFS encryption
---------------------------

If you're using *ZFS on Linux* 0.8.x or higher, you can use native
encryption. You should enable this on the pool directly. Now *all child
datasets* will use encryption.

Don't worry about the key just yet. You can always change it, as it is a
*wrapping key* only; that is, *the key is used to decrypt the real key
which never changes.*

For now, start out with a passphrase key:

.. code-block:: console

    # pwgen -s 512 1
    abcdef...


--------------------
Setting up the zpool
--------------------

If you prepared which disks you'll be using according to the method
described above, you now have a ``disks`` file with a destination
"number" and a disk identifier.

Setting up three *vdevs* and a set of spares is then as easy as this:

.. code-block:: console

    # zpool create -o ashift=12 \
        -O canmount=off -O xattr=sa \
        -O compression=lz4 -O encryption=aes-256-gcm \
        -O keylocation=prompt -O keyformat=passphrase \
        tank raidz2 \
        $(awk '/^0 /{print "disk/by-id/" $2}' disks)

    # zpool add tank raidz2 $(awk '/^1 /{print "disk/by-id/" $2}' disks)

    # zpool add tank raidz2 $(awk '/^2 /{print "disk/by-id/" $2}' disks)

    # zpool add tank spare $(awk '/^S /{print "disk/by-id/" $2}' disks)

Check the ``zpool status``:

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
        raidz2-2                            ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
      spares
        scsi-SSEAGATE_ST10000NM0226_9866    AVAIL
        scsi-SSEAGATE_ST10000NM0226_5992    AVAIL
        scsi-SSEAGATE_ST10000NM0226_5900    AVAIL
        scsi-SSEAGATE_ST10000NM0226_8412    AVAIL

Nice and shiny!

With:

* readable device IDs instead of *kernel-generated sdX numbers*;
* shuffled disks to reduce the chance of a batch of bad disks ending up
  on the same vdev.


-------------------------------
Explanation of zpool attributes
-------------------------------

vdevs and raidz2
~~~~~~~~~~~~~~~~

A *ZFS pool* consists of one or more *vdevs* (and optionally spares,
cache and log disks).

Every *vdev* itself must be redundant. *If one of the vdevs fails, your
entire pool fails.* A *minimal* safe setup would be:

* one *vdev* with two *mirrored* disks.

By adding more *vdevs*, *ZFS* will do a *form of striping* on those
(i.e. more read/write speed). It makes sense to make all *vdevs* equal in
size, but it is not mandatory.

Our setup uses:

* three *vdevs* with ten *raidz2* disks per *vdev* and four *spare* disks.

*raidz2* is the *ZFS* equivalent of *raid6*; in our case 8 data disks and 2
parity disks. Two disks in the same *vdev* are allowed to fail. And upon
failure, a spare is automatically activated.

zpool create options
~~~~~~~~~~~~~~~~~~~~

In the create commands above, we use ``ashift=12``, ``canmount=off``,
``xattr=sa``, ``compression=lz4`` and ``encryption=aes-256-gcm``:

* ``ashift=12``: Because most newer disks emulate having 512byte sectors
  (the default ``ashift=9``) but in reality have 4K sectors
  (``ashift=12``), you'll want this option for performance.
* ``canmount=off``: Because we don't want to write in the root dataset.
* ``xattr=sa``: Lets us add extended attributes in inodes. We don't use
  them for now, but they can be nice to have later.
* ``compression=lz4``: LZ4 is a relatively fast compression scheme that
  gives you better performance, and improves the security of the
  encryption (because of the increased entropy). *(Note that we'll
  consider CRIME-based attacks (using partial compression to attack
  encryption) irrelevant on the local system.)*
* ``encryption=aes-256-gcm``: Yes. We want the best native encryption we
  can get now.


----------------------
Replacing faulty disks
----------------------

When you're dealing with a DEGRADED array, you'll want to use the ``zpool
replace`` command. Mark the failing disk offline using ``zpool offline`` and
replace that disk with the new one.

.. code-block:: console

    # zpool offline tank scsi-SSEAGATE_ST10000NM0226_0123
    # ledctl locate=/dev/disk/by-id/scsi-SSEAGATE_ST10000NM0226_0123

Swap the disks, and replace:

.. code-block:: console

    # zpool replace tank scsi-SSEAGATE_ST10000NM0226_0123 \
        /dev/disk/by-id/scsi-NEW_DISK
    # ledctl locate_off=/dev/disk/by-id/scsi-NEW_DISK

If you have trouble with the finding the original disk, use ``zdb`` to
list the disks. You can then use the ``guid`` as *old* disk when
replacing.

hot spares
~~~~~~~~~~

When the *hot spares* work as intended, a failing disk will have been
substituted by a spare already. But you'll still need to manually swap
them out to make the array completely online.

::

    # zpool status
    ...
      NAME                                  STATE
      tank                                  DEGRADED
        raidz2-0                            ONLINE
          scsi-SSEAGATE_ST10000NM0226_6351  ONLINE
          scsi-SSEAGATE_ST10000NM0226_0226  ONLINE
          scsi-SSEAGATE_ST10000NM0226_8412  ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
        raidz2-1                            DEGRADED
          spare-1                           DEGRADED
            scsi-SSEAGATE_ST10000NM0226_0123  UNAVAIL
            scsi-SSEAGATE_ST10000NM0226_9866  ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
        raidz2-2                            ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          scsi-SSEAGATE_ST10000NM0226_...   ONLINE
          ...
      spares
        scsi-SSEAGATE_ST10000NM0226_9866    INUSE
        scsi-SSEAGATE_ST10000NM0226_5992    AVAIL
        scsi-SSEAGATE_ST10000NM0226_5900    AVAIL
        scsi-SSEAGATE_ST10000NM0226_8412    AVAIL

This requires some manual action:

.. code-block:: console

    # zpool detach tank scsi-SSEAGATE_ST10000NM0226_0123

Now the array should be ONLINE again, and ``scsi-SSEAGATE_ST10000NM0226_9866``
will be gone from the spares list.

Use ``ledctl`` to find the broken disk and physically replace with a new
one, and lastly ``zpool add tank spare NEW_DISK``.
