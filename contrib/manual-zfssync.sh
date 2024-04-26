#!/bin/sh -eu

# Usage: .../manual-zfssync USER@MACHINE destprefix [--init|--prune] sources...

# Right now:
# - assuming you're running this as root
# - remote has zfs list/get powers
# - remote has 'sudo zfs send --raw' powers

# Envvars:
# - MANUAL_ZFSSYNC_OVERWRITE_NEWER_SNAPSHOTS=1

ssh_target="$1"; shift  # remotebackup@DEST
REMOTE_CMD="/usr/bin/ssh -oLogLevel=error $ssh_target"  # options?

LOCAL_PREFIX="$1"; shift  # "tank" both local and remote
REMOTE_PREFIX="$LOCAL_PREFIX"

ARGV0=manual-zfssync
MAILTO=root

KEEP_EXTRA=25

export LC_ALL=C

NOTIFY_ZABBIX_ERROR=  # no error reported so far..

notify_zabbix_job_finished() {
    if test -f /etc/zabbix/zabbix_agentd.conf; then
        zabbix_sender -c /etc/zabbix/zabbix_agentd.conf \
            -k planb.secondary.recv.time -o $(date +%s)

        if test -z "$NOTIFY_ZABBIX_ERROR"; then
            zabbix_sender -c /etc/zabbix/zabbix_agentd.conf \
                -k planb.secondary.error.msg -o ''  # no error
        fi
    fi
}

notify_zabbix_error() {
    local error="$1"
    NOTIFY_ZABBIX_ERROR=1  # there was an error..

    if test -f /etc/zabbix/zabbix_agentd.conf; then
        zabbix_sender -c /etc/zabbix/zabbix_agentd.conf \
            -k planb.secondary.error.msg -o "$error"
    fi
}

log_alert() {
    local subject="$1"
    echo "$subject" >&2
    if ! test -t 2; then
        (
            echo "$subject"
            echo
            cat
        ) | mail -s "[$ARGV0] $subject" $MAILTO
    fi
    notify_zabbix_error "$subject"
}

remote_to_local_path() {
    local remote="$1"
    # for i in $(seq $PATH_STRIP); do
    #     remote=${remote#*/}
    #     test -z "$remote" && echo "error: Path strip too far" && exit 2
    # done
    # Special case: _local contains keys
    if test "$remote" = "tank/_local"; then
        echo "tank/_local-at-${ssh_target##*@}"
    else
        echo "$LOCAL_PREFIX/${remote#$REMOTE_PREFIX/}"
    fi
}

local_to_remote_path() {
    local local="$1"
    echo "$REMOTE_PREFIX/${local#$LOCAL_PREFIX/}"
}

we_have_this_dataset() {
    local remote="$1"
    local local=$(remote_to_local_path "$remote")
    zfs get name "$local" >/dev/null 2>&1
}

recv_initial() {
    _recv "$1" init
}

recv_incrementals() {
    _recv "$1" "${2:--1}"
}

_recv() {
    local remote="$1"
    local local="$(remote_to_local_path "$remote")"
    local howmany="$2"
    flags="--raw --props"

    # Are we looking at a filesystem with subfilesystems
    # (planb:contains=filesystems). If so, fetch them, and act upon those.
    local has_contained_filesystems=false
    local children="$(timeout -s9 120s $REMOTE_CMD \
        "test \"\$(sudo zfs get -Hovalue planb:contains '$remote')\" \
         = filesystems && sudo zfs list -Honame -d 1 -t filesystem '$remote' \
         | sed -e1d")"
    if test -n "$children"; then
        if test "$(zfs get -Hovalue planb:contains "$local" 2>/dev/null)" \
                != filesystems; then
            # We cannot 'zfs create -o planb:contains=filesystems "$local"'
            # as we'll get a "encryption root's key is not loaded or provided".
            echo "info: No local containing filesystem yet. Syncing..."
            local newsnap="@planb-$(TZ=UTC date +%Y%m%dT%H%MZ)"
            timeout -s9 120s $REMOTE_CMD "sudo zfs snapshot '$remote$newsnap'"
            $REMOTE_CMD "sudo zfs send $flags '$remote$newsnap'" |
                zfs recv -u -o atime=off -o readonly=on "$local"
            howmany=init
        fi
        local child=
        echo "info: $remote has subfilesystems:" \
            $(echo "$children" | sed -e 's#.*/#./#')
        for child in $children; do
            case $child in
                $remote/*) :;;  # sane
                *) echo $children | log_alert "fatal $child"; exit 1;; # insane
            esac
            local childlocal="$(remote_to_local_path "$child")"
            local childhowmany="$howmany"
            zfs list -Honame -d 0 "$childlocal" >/dev/null 2>&1 ||
                childhowmany=init
            _recv "$child" "$childhowmany"
            has_contained_filesystems=true
        done
        test $howmany != init || howmany=3  # cannot init if we're here
    fi

    local theirsnaps
    local remotesnap
    local commonsnap=""
    local newer_snapshots=""

    # Get remote snapshots.
    theirsnaps=$(timeout -s9 120s $REMOTE_CMD \
        "sudo zfs list -H -d 1 -t snapshot -S creation -o name '$remote'" |
        sed -e '/@planb-/!d;s/[^@]*@/@/')
    if test -z "$theirsnaps"; then
        if $has_contained_filesystems; then
            # All good, we did something.
            return
        else
            # Not good, we appear to be looking for a fileset that does not
            # exist. Or at least a fileset without snapshots; which sounds
            # very unlike a "double backup" fileset.
            log_alert "Error: 0 remote snapshots (peer-zfs:$remote)" </dev/null
            return
        fi
    fi

    # Special hackery for tank/_local. Because it is not auto-snapshotted, we
    # need to manually make some.
    if test "$remote" = tank/_local; then
        local newsnap="@planb-$(TZ=UTC date +%Y%m%dT%H%MZ)"
        remotesnap=$(echo "$theirsnaps" | head -n1)
        timeout -s9 120s $REMOTE_CMD \
            "if sudo zfs diff -H '$remote$remotesnap' | grep -q ^; then \
             sudo zfs snapshot '$remote$newsnap'; fi"
        remotesnap=
        theirsnaps=$(timeout -s9 120s $REMOTE_CMD \
            "sudo zfs list -H -d 1 -t snapshot -S creation -o name '$remote'" |
            sed -e '/@planb-/!d;s/[^@]*@/@/')
    fi

    if test "$howmany" = init; then
        # Take oldest remote snapshot as a starting point. Don't try to do all
        # data immediately.
        remotesnap=$(echo "$theirsnaps" | tail -n1)
        test -n "$remotesnap" && remotesnap="${remote}${remotesnap}"
    else
        # Take their snapshots and ours. And find the first match.
        local oursnaps
        oursnaps=$(zfs list -H -d 1 -t snapshot -S creation -o name "$local" |
            sed -e '/@planb-/!d;s/[^@]*@/@/')
        if test -z "$oursnaps"; then
            log_alert "Impossible: no local snapshots (zfs:$local)" </dev/null
            exit 2
        fi
        remotesnap="$(echo "$theirsnaps" | head -n1)"
        local match
        for match in $oursnaps; do
            if echo "$theirsnaps" | grep -q "^$match$"; then
                commonsnap=$match  # @planb-12345
                break
            fi
            newer_snapshots="${newer_snapshots} $match"
        done
        newer_snapshots=${newer_snapshots# }

        if test "$remotesnap" = "$commonsnap"; then
            echo "info: We already have $remote$remotesnap" >&2
            return
        fi
        if test -z "$commonsnap"; then
            (
                echo "$oursnaps" | sed -e 's/^/- /'
                echo "$theirsnaps" | sed -e 's/^/+ /'
            ) | log_alert "Problem: no common snapshots (zfs:$local)"
            return
        fi

        # howmany holds how many snapshots we want.
        if test "$howmany" -ne -1; then
            # Look upwards from commonsnap using grep -B <howmany>.
            remotesnap=$(
                echo "$theirsnaps" | grep -B$howmany "^$commonsnap$" |
                    head -n1)
        fi
        test -n "$remotesnap" && remotesnap="${remote}${remotesnap}"
    fi

    if test -z "$remotesnap"; then
        echo "error: No planb snapshots on $remote; skipping" >&2
        false
        return
    fi

    remote_arg="\"$remotesnap\""
    test -n "$commonsnap" && remote_arg="-I \"$commonsnap\" $remote_arg"

    sizestr=$(timeout -s9 300s $REMOTE_CMD \
        "sudo zfs send $flags --dryrun --parsable $remote_arg")
    size=$(echo "$sizestr" |
        sed -e '/^size[[:blank:]]/!d;s/^size[[:blank:]]\+//')
    if test -z "$size"; then
        echo "error: No size, got: $sizestr" >&2
        false
        return
    fi
    local human_size=$(printf '%015d' $size |
        sed -e 's/\([0-9]\{3\}\)/\1,/g;s/^[0,]*//;s/,$//')
    echo "info: Retrieving $human_size bytes from" \
         "$remote${commonsnap:-@(void)}..${remotesnap#*@}" >&2

    # Fetch the data from remote.
    #
    # Setting atime=off so mounting (without -o ro,noatime) does not affect
    # the mounted area. If we have atime, then a single mount is enough to
    # break incremental transfers:
    #
    # > # zfs list -r -t all tank/X | tail -n3
    # > tank/X@planb-20220117T0844Z  96.0M      -      108G  -
    # > tank/X@planb-20220118T0103Z  98.0M      -      108G  -
    # > tank/X@planb-20220118T0934Z     0B      -      108G  -
    #
    # > # zfs mount tank/X
    # >
    # > # zfs list -r -t all tank/X | tail -n3
    # > tank/X@planb-20220117T0844Z  96.0M      -      108G  -
    # > tank/X@planb-20220118T0103Z  98.0M      -      108G  -
    # > tank/X@planb-20220118T0934Z  84.9M      -      108G  -
    #
    # > # zfs diff tank/X@planb-20220118T0934Z
    # > (no change)
    #
    # > # ... | zfs recv
    # > ...
    # > cannot receive incremental stream: destination X has been modified
    # > since most recent snapshot
    #
    # Also, add -u to _not_ mount the filesystem after/during recv.
    local zfs_recv_force=
    if test -n "$newer_snapshots"; then
        if test "${MANUAL_ZFSSYNC_OVERWRITE_NEWER_SNAPSHOTS:-0}" != 0; then
            zfs_recv_force=-F
            echo "warning: We have newer snapshots ($newer_snapshots) after" \
               "$remote${commonsnap:-@(void)}, adding recv -F" >&2
        else
            echo "warning: We have never snapshots ($newer_snapshots) after" \
               "$remote${commonsnap:-@(void)}, expect failure" >&2
        fi
    fi
    $REMOTE_CMD "sudo zfs send $flags $remote_arg" |
        pv --average-rate --bytes --eta --progress --eta \
            --size "$size" --width 72 |
            zfs recv $zfs_recv_force -u -o atime=off -o readonly=on "$local"
}

recv_initial_and_some_incrementals() {
    local remotepath="$1"
    local err=0
    if ! recv_initial "$remotepath"; then
        err=1
    else
        # Fetch two incrementals immediately. To avoid the
        # problematic case when a snapshot is about to get
        # destroyed.
        recv_incrementals "$remotepath" 2 || err=2
    fi
    if test $err -ne 0; then
        log_alert "Problem: init/inc ($err) error (peer-zfs:$remotepath)" \
            </dev/null
        false
    fi
}


init() {
    local remotepath
    for remotepath in "$@"; do
        if ! we_have_this_dataset "$remotepath"; then
            recv_initial_and_some_incrementals "$remotepath" || true
        fi
    done
}

init_and_increment() {
    local remotepath
    for remotepath in "$@"; do
        if we_have_this_dataset "$remotepath"; then
            # Fetch three snapshots
            if ! recv_incrementals "$remotepath" 3; then
                log_alert "Problem: inc error (peer-zfs:$remotepath)" \
                    </dev/null
            fi
        else
            # Fetch the initial plus some snapshots
            recv_initial_and_some_incrementals "$remotepath" || true
        fi
    done
}

prune() {
    if test -n "$*"; then
        echo "Unexpected args for prune..." >&2
        exit 1
    fi
    local dataset
    local ourdatasets
    ourdatasets=$(
        zfs list -Honame -r -t filesystem "$LOCAL_PREFIX" &&
        zfs list -Honame -r -t volume "$LOCAL_PREFIX" ) || exit 1
    ourdatasets=$(echo "$ourdatasets" | sort |
        grep -vE "^$LOCAL_PREFIX(/_local(-.*)?)?\$")
    for dataset in $ourdatasets; do
        if ! prune_dataset "$dataset"; then
            echo "Failure during $dataset .. continuing" >&2
        fi
    done
}

prune_dataset() {
    local local="$1"
    local remote=$(local_to_remote_path "$local")
    local oursnaps
    local theirsnaps
    oursnaps=$(zfs list -H -d 1 -t snapshot -S creation -o name "$local" |
        sed -e '/@planb-/!d;s/[^@]*@/@/')
    if test -z "$oursnaps"; then
        echo "critical: Impossible $local" >&2
        false
        return
    fi
    theirsnaps=$(timeout -s9 120s $REMOTE_CMD \
        "sudo zfs list -H -d 1 -t snapshot -S creation -o name '$remote'" |
        sed -e '/@planb-/!d;s/[^@]*@/@/')
    local ourtmp=$(mktemp)
    local theirtmp=$(mktemp)
    echo "$oursnaps" >"$ourtmp"
    echo "$theirsnaps" >"$theirtmp"
    local diffsnaps="$(
        diff --minimal -U1000 "$ourtmp" "$theirtmp" |
        sed -e '1,4d')"
    rm "$ourtmp" "$theirtmp"
    if test -z "$diffsnaps"; then
        # No difference.. all done.
        echo "Nothing to prune for (local $local), no diffs"
        return
    fi
    if ! echo "$diffsnaps" | grep -q '^ '; then
        echo "No common snapshots (local $local):" >&2
        echo "$diffsnaps"
        echo "($local)"
        false
        return
    fi
    local prunesnaps="$(
        echo "$diffsnaps" | sed -e '/^-/!d;s/^-//' |
        sed -e '1,'$KEEP_EXTRA'd' | tac)"  # keep N extra, del oldest first
    if test -z "$prunesnaps"; then
        local keepcount="$(echo "$diffsnaps" | grep ^- | wc -l)"
        echo "Nothing to prune for (local $local), keeping $keepcount spare"
        return
    fi
    echo "Pruning $(echo "$prunesnaps" | wc -l) snapshots from $local..."
    local snap
    local n=0
    for snap in $prunesnaps; do
        n=$((n+1))
        echo -n " $n"
        zfs destroy "$local$snap"
    done
    echo .
}


case "${1:-}" in
--prune)
    shift
    prune "$@"
    ;;
--init)
    shift
    # Special case: _local contains keys
    init "tank/_local"
    init "$@"
    ;;
-*)
    echo "Unexpected option $1" >&2
    exit 1
    ;;
*)
    # Special case: _local contains keys
    init_and_increment "tank/_local"
    init_and_increment "$@"
    if test $? -eq 0; then notify_zabbix_job_finished; fi
    ;;
esac
