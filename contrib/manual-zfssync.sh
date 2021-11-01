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

export LC_ALL=C

remote_to_local_path() {
    local remote="$1"
    # for i in $(seq $PATH_STRIP); do
    #     remote=${remote#*/}
    #     test -z "$remote" && echo "error: Path strip too far" && exit 2
    # done
    echo "$LOCAL_PREFIX/${remote#$REMOTE_PREFIX/}"
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

    local remotesnap
    local commonsnap=""
    if test "$howmany" = init; then
        # Take oldest remote snapshot as a starting point. Don't try to do all
        # data immediately.
        remotesnap=$($REMOTE_CMD \
            "zfs list -H -d 1 -t snapshot -s creation -o name \"$remote\"" |
            sed -ne '/@planb-/{p;q}')
    else
        # Take their snapshots and ours. And find the first match.
        local oursnaps
        local theirsnaps
        oursnaps=$(zfs list -H -d 1 -t snapshot -S creation -o name "$local" |
            sed -e '/@planb-/!d;s/[^@]*@/@/')
        test -z "$oursnaps" && echo "critical: Impossible" >&2 && exit 2
        theirsnaps=$($REMOTE_CMD \
            "zfs list -H -d 1 -t snapshot -S creation -o name \"$remote\"" |
            sed -e '/@planb-/!d;s/[^@]*@/@/')
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
        test -z "$commonsnap" && echo "critical: No common snap" >&2 && exit 2

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

    sizestr=$($REMOTE_CMD \
        "sudo zfs send $flags --dryrun --parsable $remote_arg")
    size=$(echo "$sizestr" |
        sed -e '/^size[[:blank:]]/!d;s/^size[[:blank:]]\+//')
    if test -z "$size"; then
        echo "error: No size, got: $sizestr" >&2
        false
        return
    fi
    echo "info: Retrieving $size bytes from\
 $remote${commonsnap:-@(void)}..${remotesnap#*@}" >&2
    $REMOTE_CMD "sudo zfs send $flags $remote_arg" |
        pv --average-rate --bytes --eta --progress --eta \
            --size "$size" --width 72 | zfs recv "$local"
}

init() {
    local remotepath
    for remotepath in "$@"; do
        if ! we_have_this_dataset "$remotepath"; then
            if recv_initial "$remotepath"; then
                # Fetch two incrementals immediately. In case the oldest snapshot
                # is about to get destroyed.
                recv_incrementals "$remotepath" 2 ||
                    echo "sad times.. continuing.." >&2
            else
                echo "sad times.. continuing.." >&2
            fi
        fi
    done
}

init_and_increment() {
    local remotepath
    for remotepath in "$@"; do
        if we_have_this_dataset "$remotepath"; then
            recv_incrementals "$remotepath" 1 ||
                echo "sad times.. continuing.." >&2
        else
            if recv_initial "$remotepath"; then
                # Fetch two incrementals immediately. In case the oldest snapshot
                # is about to get destroyed.
                recv_incrementals "$remotepath" 2 ||
                    echo "sad times.. continuing.." >&2
            else
                echo "sad times.. continuing.." >&2
            fi
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
        grep -vE "^$LOCAL_PREFIX(/_local)?\$")
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
    theirsnaps=$($REMOTE_CMD \
        "zfs list -H -d 1 -t snapshot -S creation -o name \"$remote\"" |
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


case "$1" in
--prune)
    shift
    prune "$@"
    ;;
--init)
    shift
    init "$@"
    ;;
-*)
    echo "Unexpected option $1" >&2
    exit 1
    ;;
*)
    init_and_increment "$@"
    ;;
esac
