#!/bin/sh
export LC_ALL=C
export LC_CTYPE=C
set -u

! test $(id -u) = 0 && echo "must be root" >&2 && exit 1

used_keylocations=$(
    zfs list -Hokeylocation |
    sed -e '/^none$/d;/^file:\/\//!d;s/^file:\/\///' |
    sort -V)

# Format sanity check
echo "* sanity check"
if ! echo "$used_keylocations" |
        grep -q '^/tank/_local/zfskeys/tank/osso-walter-prive/_key[.]bin$'; then
    cut -c 5- >&2 << '    EOF'
    Sanity check failed. Expected a certain entry to be found. Format changed?
    Or perhaps you haven't mounted /tank/_local yet?
    EOF
    exit 1
fi

# Duplicate check
echo "* duplicate check"
if echo "$used_keylocations" | uniq -c |
        grep -E '^[[:blank:]]*([2-9]|1[0-9])' >&2; then
    echo " ^-- duplicate entries detected (WARNING)" >&2
    echo >&2
fi

# Do all referenced files exist
echo "* check that all referenced keys exist"
ret=0
echo "$used_keylocations" | while read -r location; do
    if ! test -s "$location"; then
        ret=1
        found=$(zfs list -Hokeylocation,name | grep -F "file://$location" |
                sed -e 's/^[^[:blank:]]*[[:blank:]]\+//')
        dataset=${found%*\t }
        if test -z "$dataset"; then
            echo "$location: missing (???)" >&2
        else
            echo "$location: missing from $dataset" >&2
            echo "  #fix?# zfs set \
keylocation=file:///tank/_local/zfskeys/$dataset/_key.bin $dataset" >&2
        fi
    fi
    test $ret -eq 0
done
if test $? -ne 0; then
    cut -c 5- >&2 << '    EOF'
     ^-- missing files detected (ERROR)
    
    Find them with: zfs list -Hokeylocation,name | grep KEY
    If zfs-quick-mount (by path) works, then they have been moved, but
    require a re-set: zfs set keylocation=NEWKEY DATASET
    # zfs set keylocation=file:///tank/_local/zfskeys/DATASET/_key.bin DATASET

    EOF
fi

# Do all files/paths that exist get referenced
echo "* check that all existing keys are referenced"
ret=0
find /tank/_local/zfskeys -mindepth 1 -type d | sort -V | while read dir; do
    if ! find "$dir" -mindepth 1 -maxdepth 1 | grep -q ''; then
        echo "$dir: no contents in directory" >&2
        ret=1
    fi
    test $ret -eq 0
done
ret=$?
find /tank/_local/zfskeys -mindepth 1 '!' -type d | sort -V | while read file; do
    fn=${file##*/}
    if test "$fn" != "_key.bin" || ! test -s "$file"; then
        echo "$file: unexpected file" >&2
        ret=1
    elif ! echo "$used_keylocations" | grep -q "^$file\$"; then
        echo "$file: does not appear to have a dataset" >&2
        ret=1
    fi
    test $ret -eq 0
done
if test $? -ne 0; then
    cut -c 5- >&2 << '    EOF'
     ^-- excess files/paths detected (WARNING)

    EOF
fi
