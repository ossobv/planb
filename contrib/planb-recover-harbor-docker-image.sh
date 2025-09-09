#!/bin/sh
# planb-recover-harbor-docker-image.sh // PlanB (OSSO B.V.) 2025
#
# Recover docker images from (backups of) the Harbor Registry.
#
# NOTE: This uses the planb-objsync.cur file for quick file search. We
# can likely bypass that, because the repository layout is consistent.
# Maybe do that later.
#
# NOTE: This re-creates docker images based on the config+contents.
# This should produce and identical Docker image, but the RepoDigest
# will not be the same. (This could be due to mtime differences.)
#
# Usage:
#
#   planb-recover-harbor-docker-image.sh \
#       /path/to/harbor/.zfs/snapshot/planb-20250909T0357Z/planb-objsync.cur \
#       /path/to/harbor/.zfs/snapshot/planb-20250909T0357Z/data \
#       sha256:aee...401 \
#       harbor.example.com/myproject/myimage:mytag
#
# That "sha256:aee...401" is the RepoDigest. If it is gone from the server,
# you'll get a:
#
#   Error response from daemon: manifest for
#   harbor.example.com/myproject/myimage@sha256:aee...401 not found:
#   manifest unknown: manifest unknown
#
# This script will look inside the "/data/" dir for the specified RepoDigest
# (in this example "sha256:aee...401"), and create a new tar ball. The
# "harbor.example.com/myproject/myimage:mytag" is the cosmetic name you'll
# give it.
#
# It will write: /tmp/harbor.example.com_myproject_myimage_mytag.tar
#
# Load it into docker using "docker load -i" and then you can push it.
#
# NOTE: Again, this will alter the RepoDigest, but the product should be
# the same.
#
set -eu

planb_objsync=${1:?requires path to planb-objsync.cur}
files_path=${2:?requires path where buckets/files are, relative to objsync}
manifest_sha256=${3:?requires sha256:xxx to restore}
image_name_and_tag=${4:?initial image_name:image_tag}
safe_image_name_and_tag=/tmp/$(printf '%s' "$image_name_and_tag" |
    sed -e 's/[^A-Za-z0-9_.-]/_/g;s/[.][.]\+/./g;s/[.]\+$//').tar

if test "${manifest_sha256#sha256:}" = "$manifest_sha256"; then
    echo "Expected manifest hash to start with 'sha256:...'" >&2
    exit 1
fi

if test "${files_path%/}" = "$files_path"; then
    files_path=$files_path/
fi

if ! test -d "$files_path"; then
    echo "Expected valid file path in '$files_path'" >&2
    exit 1
fi

if test -f "$safe_image_name_and_tag"; then
    echo "Output file '$safe_image_name_and_tag' already exists" >&2
    exit 1
fi

get_path_for_hash() {
    local hash="$1"
    grep "registry/v2/blobs/sha256/../${hash#sha256:}/" "$planb_objsync" |
        sed -e 's@|@/@;s/|.*//;s@^@'"$files_path"'@'
}

output_dir=$(mktemp -d)
trap "rm -rf '$output_dir'" EXIT
umask 0077  # so that the files get consistent perms too

manifest_path=$(get_path_for_hash "$manifest_sha256")
manifest=$(cat "$manifest_path")
manifest_mtime=$(stat -c%y "$manifest_path")
printf '%s\n' "$manifest" > "$output_dir/manifest-harbor.json"
echo "+ $output_dir/manifest-harbor.json" >&2
# NOTE: The manifest has:
# {"schemaVersion": 2,
#  "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
#  "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
#             "size": 1436,
#             "digest": "sha256:80d...642"},
#  "layers": [
#    {"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
#     "size": 30201284, "digest": "sha256:216...fac"},
#    ...
# The sha256sum of that digest (no-LFs) is the original RepoDigest.

config_sha256=$(printf '%s' "$manifest" | jq -r .config.digest)
config_path=$(get_path_for_hash "$config_sha256")
cat "$config_path" >"$output_dir/${config_sha256#sha256:}.json"
echo "+ $output_dir/${config_sha256#sha256:}.json" >&2
# NOTE: The digest has:
#   "rootfs": {
#     "type": "layers",
#     "diff_ids": [
#       "sha256:56a5c11640c87bae4229f89bfc8b114449a42e76a58d...",
#       ...
# They are sha256 of the uncompressed tar files we're creating next.

for layer_sha256 in $(jq -r '.layers[].digest' \
        "$output_dir/manifest-harbor.json"); do
    # These sha256 hashes are unrelated to the final hash we'll use.
    # Doesn't matter. Anything unique is apparently fine.
    # (Hashing the layer.tar would in fact get us the diff_id above.)
    layer_path=$(get_path_for_hash "$layer_sha256")
    zcat "$layer_path" >"$output_dir/tmp.tar"
    layer_uncompressed_sha256=$(\
        sha256sum "$output_dir/tmp.tar" | awk '{print $1}')
    layer_uncompressed_dest="$output_dir/$layer_uncompressed_sha256/layer.tar"
    mkdir -p "$(dirname "$layer_uncompressed_dest")"
    mv "$output_dir/tmp.tar" "$layer_uncompressed_dest"
    echo "+ $layer_uncompressed_dest" >&2
done

# Now we have:
# - maniferst-harbor.json
# - <digest-sha256>.json
# - <layer-sha256>/layer.tar [multiple]
# Now we want manifest.json (docker-style)
jq -c . <<EOF | tr -d '\n' >"$output_dir/manifest.json"
[
  {
    "Config": "${config_sha256#sha256:}.json",
    "RepoTags": ["${image_name_and_tag}"],
    "Layers":
$(jq .rootfs.diff_ids "$output_dir/${config_sha256#sha256:}.json")
  }
]
EOF
echo "+ $output_dir/manifest.json" >&2

# Remove the harbor-manifest and make image.
rm "$output_dir/manifest-harbor.json"
echo "- $output_dir/manifest-harbor.json" >&2
tar --sort=name --mtime="$manifest_mtime" --owner=0 --group=0 --numeric-owner \
  -cf "$safe_image_name_and_tag" -C "$output_dir" .
tar -tvf "$safe_image_name_and_tag"
echo "Wrote: $safe_image_name_and_tag"
