#!/bin/sh -x
# kubersync (PlanB contrib) // wdoekes/2020 // Public Domain
#
# kubersync is an example rsync wrapper that will backup filesystems
# available to Kubernetes, like rook.io cephfs volumes.
#
# It works like this:
# - you install a single k8s pod that gets the rook/ceph mounts;
# - this k8s pod has access to the mount points, and has the rsync binary
# - the pod does nothing but sleep;
# - until we exec into it, when we want to rsync.
#
# So, instead of calling:
# - /usr/bin/rsync --server --sender ...
# we'll do:
# - kubectl exec -it MOUNT_POD -- rsync --server --sender ...
#
# That way, we have access to K8S managed mounts, if we give those
# mounts to the MOUNT_POD.
#
# Setting it up:
#
# - choose a host with a working kubectl and access to K8s;
# - configure a 'remotebackup' user on that host (for ssh access);
# - give that user sudo powers to /usr/local/bin/kubersync:
#   > # The backup user needs to acccess the MOUNT_POD.
#   > remotebackup ALL=NOPASSWD: /usr/local/bin/kubersync --server --sender *
# - setup/configure the deployment below, adding/changing rook/ceph
#   paths as needed;
# - calling "/usr/local/bin/kubersync deploy" once, to install the
#   MOUNT_POD;
# - configure the PlanB fileset, with Rync-transport:
#   * select the hostname/IP
#   * include *only* the /cephfs-nvme1 path
#   * set the 'remotebackup' user
#   * sudo
#   * change rsync-path to /usr/local/bin/kubersync
#
# That should be sufficient to get things up and running.


# Namespace where the 'backup-mount' pod is expected to run.
namespace=backup
label=app=sleepy-mount


# Quick and easy deployment. You may need to tweak this. Run this once,
# or whenever you're changing K8s storage volumes. This installs a pod that:
# - mounts the relevant volume
# - installs rsync
# - does nothing but sleep (but allows PlanB to exec to it an rsync from there)
if test "$1" = "deploy"; then
    kubectl -n "$namespace" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backup-mount
  namespace: $namespace
spec:
  progressDeadlineSeconds: 600
  replicas: 1
  revisionHistoryLimit: 3
  selector:
    matchLabels:
      app: sleepy-mount
  template:
    metadata:
      labels:
        app: sleepy-mount
    spec:
      containers:
      - name: sleepy-mount
        image: ubuntu:latest
        args:
        - -c
        - apt-get update -q && apt-get install -y rsync &&
            while true; do sleep 900; done
        command:
        - /bin/sh
        imagePullPolicy: Always
        terminationMessagePath: /dev/termination-log
        terminationMessagePolicy: File
        volumeMounts:
        - mountPath: /cephfs-nvme1
          name: cephfs-nvme1
      restartPolicy: Always
      schedulerName: default-scheduler
      securityContext: {}
      terminationGracePeriodSeconds: 30
      volumes:
      - flexVolume:
          driver: ceph.rook.io/rook
          fsType: ceph
          options:
            clusterNamespace: rook-ceph
            fsName: cephfs-nvme1
        name: cephfs-nvme1
EOF
    exit $?
fi


# Get sleepy-mount pod.
# FIXME: for bonus, we could add a json-path search so only Running pods are
# returned
pod=$(
  kubectl -n "$namespace" get pods -l "$label" \
    -o=jsonpath='{.items[0].metadata.name}')
test -z "$pod" && echo "$namespace/$label pod not found" >&2 exit 1

# Run rsync inside the pod.
exec kubectl -n "$namespace" exec -i "$pod" -- rsync "$@"
