#!/bin/sh -eu

# Usage: .../manual-zfssync USER@MACHINE destprefix [--init|--prune] sources...

# Right now:
# - assuming you're running this as root
# - remote has zfs list/get powers
# - remote has 'sudo zfs send --raw' powers

ssh_target="$1"; shift  # remotebackup@DEST
REMOTE_CMD="/usr/bin/ssh $ssh_target"  # options?

LOCAL_PREFIX="$1"; shift  # "tank" both local and remote
REMOTE_PREFIX="$LOCAL_PREFIX"

ARGV0=manual-zfssync
MAILTO=root

export LC_ALL=C

log_mail() {
    local subject="$1"
    echo "$subject" >&2
    (
        echo "$subject"
        echo
        cat
    ) | mail -s "[$ARGV0] $subject" $MAILTO
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
    local local=$(remote_to_local_path "$remote")
    local howmany="$2"
    flags="--raw --props"

    local theirsnaps
    local remotesnap
    local commonsnap=""

    theirsnaps=$(timeout -s9 120s $REMOTE_CMD \
        "sudo zfs list -H -d 1 -t snapshot -S creation -o name '$remote'" |
        sed -e '/@planb-/!d;s/[^@]*@/@/')

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
            log_mail "Impossible: no local snapshots (zfs:$local)" </dev/null
            exit 2
        fi
        remotesnap="$(echo "$theirsnaps" | head -n1)"
        local match
        for match in $oursnaps; do
            if echo "$theirsnaps" | grep -q "^$match$"; then
                commonsnap=$match  # @planb-12345
                break
            fi
        done
        if test "$remotesnap" = "$commonsnap"; then
            echo "info: We already have $remote$remotesnap" >&2
            return
        fi
        if test -z "$commonsnap"; then
            (
                echo "$oursnaps" | sed -e 's/^/- /'
                echo "$theirsnaps" | sed -e 's/^/+ /'
            ) | log_mail "Problem: no common snapshots (zfs:$local)"
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
    echo "info: Retrieving $size bytes from" \
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
    $REMOTE_CMD "sudo zfs send $flags $remote_arg" |
        pv --average-rate --bytes --eta --progress --eta \
            --size "$size" --width 72 |
            zfs recv -u -o atime=off -o readonly=on "$local"
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
        log_mail "Problem: init/inc ($err) error (peer-zfs:$remotepath)" \
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
                log_mail "Problem: inc error (peer-zfs:$remotepath)" \
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
        echo "Nothing to prune for (local $local)"
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
        sed -e '1,30d' | tac)"  # keep 30 extra, del oldest first
    local prunecount="$(echo "$prunesnaps" | wc -l)"
    test -z "$prunesnaps" && return
    echo "Pruning $prunecount snapshots from $local..."
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
    ;;
esac
