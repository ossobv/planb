#!/bin/sh -eux

# Usage: .../planb-zfssync [-qlz1] root@MACHINE tank/X tank/Y rpool/abc/def

env >&2
test -z "$planb_storage_name" && exit 3

case "${1:-}" in
-qlz1)
    deflate=qlzip1
    inflate=qlzcat1
    shift
    ;;
-*)
    echo "ERROR: Unknown compression $1" >&2
    exit 3
    ;;
'')
    echo "ERROR: Missing arguments.." >&2
    exit 3
    ;;
*)
    deflate=cat
    inflate=cat
    ;;
esac

# Is this the first time?
dataset=$(sudo zfs get -Hpo value type "$planb_storage_name")
test "$dataset" = "filesystem"

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


ssh_target="$1"; shift  # root@DEST
target_snapshot=$planb_snapshot_target
target_snapshot_prefix=${planb_snapshot_target%-*}
test "$target_snapshot_prefix" = "planb"  # HARDCODED (see temp 'daily|')

# Make snapshots.
for remotepath in "$@"; do
    src=$remotepath@$target_snapshot
    ssh $ssh_target sudo zfs snapshot "$src"
done

# Download snapshots.
for remotepath in "$@"; do
    our_path=$(echo "$remotepath" | sed -e 's#/#--#g')
    src=$remotepath@$target_snapshot
    dst=$planb_storage_name/$our_path
    recent_snapshot=$(sudo zfs list -d 1 -t snapshot -Hpo name \
        -S creation "$dst" | sed -e 's/.*@//;1q')
    if test -n "$recent_snapshot"; then
        # Undo any local changes (properties?)
        sudo zfs rollback "$dst@$recent_snapshot"
        src_prev=$remotepath@$recent_snapshot
        ssh $ssh_target sudo zfs send -i "$src_prev" "$src" '|' "$deflate" |
            "$inflate" | sudo zfs recv "$dst"
    else
        ssh $ssh_target sudo zfs send "$src" '|' "$deflate" |
            "$inflate" | sudo zfs recv "$dst"
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

# Keep only three snapshots on remote machine.
for remotepath in "$@"; do
    src=$remotepath@$target_snapshot
    ssh $ssh_target sudo zfs list -d 1 -Hpo name -t snapshot \
            -S creation "$remotepath" |
        grep -E "^.*@(daily|$target_snapshot_prefix)-" | sed -e '1,3d' |
        xargs --no-run-if-empty -n1 ssh $ssh_target sudo zfs destroy
done
