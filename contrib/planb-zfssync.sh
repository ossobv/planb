#!/bin/sh -eux

# Usage: .../planb-zfssync [--plain|--qlz1] root@MACHINE tank/X tank/Y rpool/abc/def
#
# KNOWN BUGS:
# - if you have multiple filesets (in the same planb, with the same guid)
#   backing up the same remote volume/fileset, the snapshots will conflict
# - if you have trouble with --raw, snapshots will already have been made;
#   and now you have a local-remote snapshot mismatch

env >&2
test -z "$planb_storage_name" && exit 3

# We prefer sending using --raw, it will keep compression/encryption.
# (We can sync encrypted filesystems without knowing the contents.)
# But, --raw does not exist in ZFS 0.6/0.7; first in 0.8 (see 'zfs version').
# Prefer raw sending (no argument), but allow --plain or --qlz1 (compressed
# transfer of plain data).
#
# See: zfs send 2>&1 | grep '^[[:blank:]]*send [[]-[^]]*w[^]]*[]] '
zfs_send_option=--raw  # (or the '-w' option)
deflate=
inflate=
case "${1:-}" in
--qlz1)
    zfs_send_option=
    deflate=qlzip1
    inflate=qlzcat1
    shift
    ;;
--plain)
    zfs_send_option=
    shift
    ;;
-*|'')
    echo "ERROR: Unknown/missing arguments '$1'" >&2
    exit 3
    ;;
*)
    ;;
esac

# Do we hava a guid?
test -n "$planb_guid"

# Is this the first time?
dataset=$(sudo zfs get -Hpo value type "$planb_storage_name")
test "$dataset" = "filesystem"

# Test that we have a working systemd-escape locally.
test "$(systemd-escape OK/O-käy)" = 'OK-O\x2dk\xc3\xa4y'
escape() {
    # Escape $1 to something that is legal in ZFS. Using systemd-escape, but
    # additionally replace the backslash ('\') with underscore ('_').
    # (And therefore, also escape the underscore as "\x5f", which then becomes
    # "_x5f".)
    # We feel this is okay. We expect mostly slashes ('/'), which will get
    # escaped to a single dash ('-').
    # NOTE: zfs dataset names only support [A-Za-z0-9:._-], so we may need
    # to escape additional characters in the future.
    # See: https://docs.oracle.com/cd/E36784_01/html/E36835/gbcpt.html
    # "ZFS Component Naming Requirements"
    systemd-escape "$1" | sed -e 's/_/\\x5f/g;s/\\/_/g'
}

contains=$(sudo zfs get -Hpo value planb:contains "$planb_storage_name")

# contains shall be '-' or 'data' or 'filesystems'
if test "$contains" != "filesystems"; then
    # Is there something in data?
    # We should be in $planb_storage_destination == 'data', because that's
    # where the "lock" is at. We should renamed that to.. let's say, _lock.
    test "$(find "$planb_storage_destination")" = \
        "$planb_storage_destination"  # no contents allowed!
    # Now, set the filesystems property on this.
    sudo zfs set planb:contains=filesystems "$planb_storage_name"
fi


ssh_target="$1"; shift  # remotebackup@DEST (options like -luser disallowed)
# XXX: todo: sanitize $1? (no spaces, no funny chars)
# XXX: todo: sanitize $HOME? (no spaces, no funny chars)

known_hosts_file="$HOME/.ssh/known_hosts.d/${ssh_target##*@}"
ssh_options="-o HashKnownHosts=no -o UserKnownHostsFile=$known_hosts_file"
if test -f "$known_hosts_file"; then
    ssh_options="$ssh_options -o StrictHostKeyChecking=yes"
else
    ssh_options="$ssh_options -o StrictHostKeyChecking=no"
fi

target_snapshot=$planb_snapshot_target
target_snapshot_prefix=${planb_snapshot_target%-*}
test "$target_snapshot_prefix" = "planb"  # (not needed, we use planb:owner)

# Make snapshots.
for remotepath in "$@"; do
    src=$remotepath@$target_snapshot
    ssh $ssh_options $ssh_target "\
        sudo zfs snapshot \"$src\" && \
        sudo zfs set planb:owner=$planb_guid \"$src\""
done

# Download snapshots.
for remotepath in "$@"; do
    our_path=$(escape "$remotepath")
    src=$remotepath@$target_snapshot
    dst=$planb_storage_name/$our_path
    recent_snapshot=$(sudo zfs list -d 1 -t snapshot -Hpo name \
        -S creation "$dst" | sed -e 's/.*@//;1q')
    if test -n "$recent_snapshot"; then
        # Undo any local changes (properties?)
        sudo zfs rollback "$dst@$recent_snapshot"
        src_prev=$remotepath@$recent_snapshot
        # Use "-I" instead of "-i" to send all manual snapshots too.
        # Unsure about the "--props" setting to send properties..
        if test -n "$deflate$inflate"; then
            ssh $ssh_options $ssh_target "\
                sudo zfs send $zfs_send_option -I \"$src_prev\" \"$src\" |\
                  \"$deflate\"" | "$inflate" | sudo zfs recv "$dst"
        else
            ssh $ssh_options $ssh_target "\
                sudo zfs send $zfs_send_option -I \"$src_prev\" \"$src\"" |
                sudo zfs recv "$dst"
        fi
    else
        if test -n "$deflate$inflate"; then
            ssh $ssh_options $ssh_target "\
                sudo zfs send $zfs_send_option \"$src\" | \"$deflate\"" |
                "$inflate" | sudo zfs recv "$dst"
        else
            ssh $ssh_options $ssh_target "\
                sudo zfs send $zfs_send_option \"$src\"" |
                sudo zfs recv "$dst"
        fi
    fi
    # Disable mounting of individual filesystems on this mount point. As doing
    # so will mess up the parent mount.
    type=$(sudo zfs get -o value -Hp type "$dst")
    case "$type" in
    filesystem)
        sudo zfs set canmount=off mountpoint=legacy "$dst"
        ;;
    volume)
        ;;
    *)
        echo "Unexpected FS type $dst: $type" >&2
        ;;
    esac
done

# Keep only three snapshots on remote machine. Filter by planb:owner=GUID.
for remotepath in "$@"; do
    src=$remotepath@$target_snapshot
    ssh $ssh_options $ssh_target "\
            sudo zfs list -d 1 -Hpo name,planb:owner -t snapshot \
            -S creation \"$remotepath\"" | grep -E \
        "^.*@(daily|$target_snapshot_prefix)-.*[[:blank:]]$planb_guid\$" |
        sed -e '1,3d' | awk '{print $1}' |
        xargs --no-run-if-empty -n1 ssh $ssh_options $ssh_target "\
            sudo zfs destroy"
done
