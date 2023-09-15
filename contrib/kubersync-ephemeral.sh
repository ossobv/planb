#!/bin/sh
# kubersync-ephemeral (PlanB contrib) // wdoekes/2023 // Public Domain
#
# kubersync-ephemeral is an example rsync wrapper that will backup
# filesystems available to Kubernetes containers, like rook.io cephfs volumes.
# This means you don't need to figure out where the physical storage is
# to back up files. You can just rsync from inside a container that has the
# same paths/permissions as your application.
#
# Usage:
#
#   kubersync-ephemeral NAMESPACE POD_OR_PODREGEX RSYNC_ARGS...
#
# You don't call this yourself, but are remote rsync will, for example
# like this:
#
#   rsync -va --rsync-path='sudo kubersync-ephemeral prod worker' \
#       REMOTE:/data k8s-prod-worker-data
#
# This script will behave like rsync, but not before it sets up the
# necessary mounts and execs into a pod that can read from the pod
# volumes.
#
# Setting it up:
#
# - Place this script in /usr/local/bin/kubersync-ephemeral on a host that
#   has access to kubectl with enough permissions.
#
# - Set this in sudoers file:
#
#     remotebackup ALL=NOPASSWD: /usr/local/bin/kubersync-ephemeral *
#
# - Configure PlanB fileset, set includes/excludes as normal, but set
#   rsync_path to:
#
#     /usr/local/bin/kubersync-ephemeral NAMESPACE POD_NAME_OR_REGEX
#
# Details:
# - An ephemeral rsync container that stays alive (sleeps forever) it spawned.
#   (It uses the pod spec to find out which volumeMounts are needed. They are
#   also mounted in this container.)
# - We exec into this new container whenever we want to rsync.
#
# Requirements/caveats:
# - This script expects access to a kubectl with sufficient permissions.
# - The container gets its own root filesystem: files written inside the
#   application container that is _not_ in a volumeMount will not be seen.
# - The pod name exact match is tried first. If not found a regex match
#   is attempted.
# - Ephemeral container does not ship by default until Kubernetes 1.25+.
# - Ephemeral containers will not do subPath (single file) volumeMounts. They
#   are generally only used for config files, so that's likely not a problem.
# - Ephemeral containers never disappear completely (you can kill them,
#   but they are recorded and their name cannot be reused).

set -eu

# Get args: NAMESPACE POD_NAME_OR_REGEX
namespace=$1
pod=$2
#container=XX # --target=$container
shift; shift

# Our container name:
kubersync_container=kubersync


make_ephemeral_using_debug() {
    local namespace="$1"
    local pod="$2"
    local container="$3"
    kubectl -n "$namespace" debug "$pod" --quiet=true --attach=false \
        --arguments-only=true --container="$container" \
        --image=harbor.osso.io/ossobv/rsync --
}

# Instead of spawning using 'kubectl debug', we'll do it manually.
# Otherwise we do not get access to the volumes.
# See: https://iximiuz.com/en/posts/kubernetes-ephemeral-containers/
make_ephemeral_using_curl() {
    local namespace="$1"
    local pod="$2"
    local container="$3"
    local tmpdir
    local proxypid
    local volumemounts

    # Create a temp dir and spawn a kubectl proxy.
    if ! tmpdir=$(mktemp -d); then
        echo "$0: error: failed to create temp dir" >&2
        exit 1
    fi
    trap "rm -rf '$tmpdir'" EXIT
    kubectl proxy -u "$tmpdir/k8s.sock" >&2 &
    proxypid=$!
    sleep 1  # give the proxy time to start

    # Check that we have a proxy.
    if ! kill -0 "$proxypid"; then
        echo "$0: error: proxy never started (or stopped already)" >&2
        exit 1
    fi
    trap "kill -9 $proxypid; rm -rf '$tmpdir'" EXIT

    # Get volumemounts in json format, but not those with subPath as they do
    # not work with ephemeral.
    volumemounts=$(\
        curl -sSfL -XGET --unix-socket "$tmpdir/k8s.sock" --max-time 10 \
        http://localhost/api/v1/namespaces/$namespace/pods/$pod |
        jq -c \
        '.spec.containers[0].volumeMounts | map(select(has("subPath") | not))')

    echo "$0: debug: planning to mount: $volumemounts" >&2
    out=$(curl -sSfL --unix-socket  "$tmpdir/k8s.sock" --max-time 10 \
 http://localhost/api/v1/namespaces/$namespace/pods/$pod/ephemeralcontainers \
        -XPATCH -H 'Content-Type: application/strategic-merge-patch+json' -d '
    {
        "spec":
        {
            "ephemeralContainers":
            [
                {
                    "name": "'"$kubersync_container"'",
                    "image": "harbor.osso.io/ossobv/rsync",
                    "stdin": false,
                    "tty": false,
                    "volumeMounts": '"$volumemounts"'
                }
            ]
        }
    }')
    echo "$0: debug: ephemeral container made?" >&2

    # We're done with the proxy. Kill it.
    kill -9 $proxypid
    rm -rf "$tmpdir"
    trap '' EXIT
}


# pod name cannot include a slash, as that interferes with the regex.
if test "$pod" != "${pod%/*}"; then
    # get first matching pods
    echo "$0: error: illegal slash in $namespace.$pod pod format" >&2
    exit
fi

# Check that we have jq and curl.
if ! jq --version >/dev/null || ! curl --version >/dev/null; then
    echo "$0: error: missing jq or curl" >&2
    exit 1
fi

# Check if an exact pod name match exists. Otherwise find by regex.
if kubectl -n "$namespace" get pods -o name "$pod" >/dev/null 2>&1; then
    # we have a pod
    echo "$0: debug: $namespace.$pod found" >&2
elif pod=$(kubectl get pods -n acceptatie2 -o name 2>/dev/null |
        awk -F/ '/^pod\/'$pod'/{s=$2;exit} END{if(!s)exit 1;print s}'); then
    # we have a pod
    echo "$0: debug: $namespace.$pod selected" >&2
else
    # get first matching pods
    echo "$0: error: no $namespace.$pod pod found" >&2
    exit 1
fi


# We'd like to do --attach=true --arguments-only=false -- rsync $@
# But we cannot clean up the container, because ephemeral containers cannot be
# destroyed:
# https://github.com/kubernetes/kubernetes/pull/103354#issuecomment-897451068
# https://github.com/kubernetes/kubernetes/issues/84764#issuecomment-1219310167
# https://github.com/kubernetes/enhancements/issues/3163

# Instead we'll spawn the ephemeral container (if it doesn't exist yet),
# and then connect to the running one. It will sleep forever when we pass no
# args.

have_a_running_container=$(
    kubectl -n "$namespace" get pod "$pod" \
        -o=jsonpath='{.spec.ephemeralContainers}' |
    jq -r 'map(select(.name == "'"$kubersync_container"'"))[0].name')
if test "$have_a_running_container" = "$kubersync_container"; then
    echo "$0: debug: $namespace.$pod has container $kubersync_container" >&2
else
    echo "$0: debug: $namespace.$pod needs container $kubersync_container" >&2

    # Old style. Does not have volume mounts.
    #make_ephemeral_using_debug "$namespace" "$pod" "$kubersync_container"
    # New curl style. Has volume mounts.
    make_ephemeral_using_curl "$namespace" "$pod" "$kubersync_container"

    # Sleep a while and hope we have a container.
    sleep 8
fi

# Start the rsync job. If the sidecar/ephemeral container is destroyed for some
# reason, we won't be able to attach, and things will fail. Leave that to the
# administrators.
echo "$0: debug: starting: $@" >&2
exec kubectl -n "$namespace" exec "$pod" --stdin=true --quiet=true \
    --container=$kubersync_container -- rsync "$@"
