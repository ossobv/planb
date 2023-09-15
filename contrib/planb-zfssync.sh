#!/bin/sh -eu

# Usage: .../planb-zfssync [--recursive] [--lz4|--plain|--qlz1]
#        root@MACHINE DISKS..
#
# Where DISKS are one or more of:
#   tank/X
#   rpool/X/Z
#   rpool/X:renamed-to-something
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
zfs_recursive=false
zfs_send_option=--raw  # (or the '-w' option)  # XXX DO NOT USE FOR UNENCRYPTED
zfs_recv_option='-o readonly=on'
deflate=
inflate=
if test "${1:-}" = --recursive; then
    zfs_recursive=true
    # BEWARE: Recursive sync is fragile. When it's broken, you may need to zfs
    # rollback on dest (manually recursing) so that all snapshots are at the
    # same point in time again (destroy newer snapshots on source).
    # Just adding -F does not cut it.
    #zfs_recv_option="$zfs_recv_option -F"  # force our way out of madness
    shift
fi
case "${1:-}" in
--lz4)
    zfs_send_option='--compressed --large-block'
    zfs_recv_option="$zfs_recv_option -x encryption"
    shift;
    ;;
--qlz1)
    zfs_send_option=
    zfs_recv_option="$zfs_recv_option -x encryption"
    deflate=qlzip1
    inflate=qlzcat1
    shift
    ;;
--plain)
    zfs_send_option=
    zfs_recv_option="$zfs_recv_option -x encryption"
    shift
    ;;
-*|'')
    echo "ERROR: Unknown/missing arguments '$1'" >&2
    exit 3
    ;;
*)
    ;;
esac

# Do we hava a non-empty guid and it has no whitespace?
tab=$(printf '\t')
test -n "$planb_guid" && test "${planb_guid%[ $tab]*}" = "$planb_guid"

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
    # where the "lock" is at. We should rename that to, let's say, _lock.
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
# XXX: do we?
test "$target_snapshot_prefix" = "planb"  # (not needed, we use planb:owner)

# Prepare globals, so we know what to refactor
ZFS_SEND_OPTION=$zfs_send_option
ZFS_RECV_OPTION=$zfs_recv_option
ZFS_RECURSIVE=$zfs_recursive
SSH_OPTIONS=$ssh_options
SSH_TARGET=$ssh_target
TARGET_SNAPSHOT=$target_snapshot
TARGET_SNAPSHOT_PREFIX=$target_snapshot_prefix
PLANB_GUID=$planb_guid
PLANB_STORAGE_NAME=$planb_storage_name
DEFLATE=$deflate
INFLATE=$inflate

process_zfssync_arg() {
    local snapshot_handler="$1"
    local remotepath_localpath="$2"
    local remotepath our_path dst

    # The paths to backup may be:
    #   rpool/a/b/c
    # or:
    #   rpool/a/b/c:pretty-name
    if test "${remotepath_localpath#*:}" != "$remotepath_localpath"; then
        remotepath=${remotepath_localpath%%:*}
        our_path=${remotepath_localpath#*:}
    else
        remotepath=$remotepath_localpath
        our_path=$(escape "$remotepath")
    fi
    dst=$PLANB_STORAGE_NAME/$our_path

    $snapshot_handler "$remotepath" "$dst"
}

download_snapshot() {
    local remotepath="$1"
    local localpath="$2"  # unused
    local zfs_recv_opt="$ZFS_RECV_OPTION"

    # Disable mounting of individual filesystems on this mount point.
    # Mounting those here would mess up the parent mount.
    local type="$(sudo zfs get -o value -Hp type "$localpath" 2>/dev/null ||
        ssh $SSH_OPTIONS $SSH_TARGET \
          "sudo zfs get -o value -Hp type '$remotepath'")"
    case "$type" in
    filesystem)
        # No automounting, especially not on their regular paths. Do not do
        # this afterwards (using zfs set), as mount attempts may be done
        # already.
        zfs_recv_opt="$zfs_recv_opt -o canmount=off -o mountpoint=legacy"
        ;;
    volume)
        # cannot receive incremental stream: property 'canmount' does not apply
        #   to datasets of this type
        ;;
    *)
        echo "Unexpected FS type $localpath: $type" >&2
        exit 1
        ;;
    esac

    # Ensure there is a snapshot for us.
    local recent_snapshot=$(sudo zfs list -d 1 -t snapshot -Hpo name \
        -S creation "$localpath" | sed -e 's/.*@//;1q')
    local prev_target_snapshot
    local src
    local tmp_opt
    if test -z "$recent_snapshot"; then
        # Nothing yet. See if there is an old snapshot we can start from
        # remotely. This is quite useful when testing different snapshot
        # configurations.
        prev_target_snapshot=$(ssh $SSH_OPTIONS $SSH_TARGET "\
            sudo zfs list -d 1 -Hpo name,planb:owner -t snapshot \
            -S creation \"$remotepath\"" | grep -E \
            "^.*@($TARGET_SNAPSHOT_PREFIX)-.*[[:blank:]]$PLANB_GUID\$" |
            sed -e 's/^[^@]*@//;s/[[:blank:]].*//;1q')
        if test -z "$prev_target_snapshot"; then
            # Does not exist. Create.
            $ZFS_RECURSIVE && tmp_opt=-r || tmp_opt=
            src=$remotepath@$TARGET_SNAPSHOT
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs snapshot $tmp_opt \"$src\" && \
                sudo zfs set planb:owner=$PLANB_GUID \"$src\""
        else
            # Exists, use that.
            src=$remotepath@$prev_target_snapshot
        fi
    else
        # There was a recent snapshot locally. Make a new one remotely.
        $ZFS_RECURSIVE && tmp_opt=-r || tmp_opt=
        src=$remotepath@$TARGET_SNAPSHOT
        if $ZFS_RECURSIVE; then
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs snapshot -r \"$src\" && \
                sudo zfs list -H -o name -r -t snapshot | \
                grep '@$TARGET_SNAPSHOT\$' | \
                xargs sudo zfs set planb:owner=$PLANB_GUID"
        else
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs snapshot \"$src\" && \
                sudo zfs set planb:owner=$PLANB_GUID \"$src\""
        fi
    fi

    $ZFS_RECURSIVE && tmp_opt=-R || tmp_opt=
    if test -n "$recent_snapshot"; then
        # Undo any local changes (properties?)
        # XXX: Aargh. For the fragile --recursive backups we need to do this
        # recursively (manually).
        sudo zfs rollback "$localpath@$recent_snapshot"
        local src_prev="$remotepath@$recent_snapshot"
        # Use "-I" instead of "-i" to send all manual snapshots too.
        # Unsure about the "--props" setting to send properties..
        if test -n "$DEFLATE$INFLATE"; then
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs send $tmp_opt $ZFS_SEND_OPTION -I \"$src_prev\" \
                  \"$src\" | \"$DEFLATE\"" | "$INFLATE" |
                  sudo zfs recv $zfs_recv_opt "$localpath"
        else
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs send $tmp_opt $ZFS_SEND_OPTION -I \"$src_prev\" \
                \"$src\"" |
                sudo zfs recv $zfs_recv_opt "$localpath"
        fi
    else
        if test -n "$DEFLATE$INFLATE"; then
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs send $tmp_opt $ZFS_SEND_OPTION \"$src\" | \
                \"$DEFLATE\"" | "$INFLATE" |
                sudo zfs recv $zfs_recv_opt "$localpath"
        else
            ssh $SSH_OPTIONS $SSH_TARGET "\
                sudo zfs send $tmp_opt $ZFS_SEND_OPTION \"$src\"" |
                sudo zfs recv $zfs_recv_opt "$localpath"
        fi
    fi
}

prune_remote_snapshots() {
    # Keep only three snapshots on remote machine. Filter by planb:owner=GUID.
    local remotepath="$1"
    local localpath="$2"  # unused

    if $ZFS_RECURSIVE; then
        ssh $SSH_OPTIONS $SSH_TARGET "\
            sudo zfs list -Honame -r -t filesystem,volume \"$remotepath\" | \
            while read -r fs; do sudo zfs list -d 1 -Hpo planb:owner,name \
                -t snapshot -S creation \"\$fs\" | \
            grep -E '^$PLANB_GUID[[:blank:]].*@($TARGET_SNAPSHOT_PREFIX)-' | \
            sed -e '1,3d;s/^$PLANB_GUID[[:blank:]]//'; done | \
            xargs --no-run-if-empty -d'\\n' -n1 sudo zfs destroy"
    else
        ssh $SSH_OPTIONS $SSH_TARGET "\
            sudo zfs list -d 1 -Hpo planb:owner,name -t snapshot \
              -S creation \"$remotepath\" | \
            grep -E '^$PLANB_GUID[[:blank:]].*@($TARGET_SNAPSHOT_PREFIX)-' | \
            sed -e '1,3d;s/^$PLANB_GUID[[:blank:]]//' | \
            xargs --no-run-if-empty -d'\\n' -n1 sudo zfs destroy"
    fi
}


# Download snapshots (make them on remote if necessary).
for arg in "$@"; do
    process_zfssync_arg download_snapshot "$arg"
done

# Prune most snapshots from remote machine.
for arg in "$@"; do
    process_zfssync_arg prune_remote_snapshots "$arg"
done
