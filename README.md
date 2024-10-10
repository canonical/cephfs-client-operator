# CephFS client operator

A [Juju](https://juju.is) operator for mounting Ceph filesystems.

[![Charmhub Badge](https://charmhub.io/cephfs-client/badge.svg)](https://charmhub.io/cephfs-client)
[![CI](https://github.com/canonical/cephfs-client-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/cephfs-client-operator/actions/workflows/ci.yaml/badge.svg)
[![Publish](https://github.com/canonical/cephfs-client-operator/actions/workflows/publish.yaml/badge.svg)](https://github.com/canonical/cephfs-client-operator/actions/workflows/publish.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)


The CephFS client operator requests and mounts exported Ceph filesystems on virtual machines. Ceph File System (CephFS)
is a POSIX-compliant file system built on top of Ceph’s distributed object store, RADOS.

## ✨ Getting started 

#### With Microceph

1. Deploy microceph, ceph-fs, cephfs-client, and a machine to mount the filesystem on: 

```shell
juju add-model ceph
juju deploy -n 3 microceph \
  --channel latest/edge \
  --storage osd-standalone='2G,3' \
  --constraints="virt-type=virtual-machine root-disk=10G mem=4G"
juju deploy ceph-fs --channel latest/edge
juju deploy cephfs-client data --channel latest/edge --config mountpoint=/data
juju deploy ubuntu --base ubuntu@22.04 --constraints virt-type=virtual-machine
```

2. Integrate everything, and that's it!

```shell
juju integrate microceph:mds ceph-fs:ceph-mds
juju integrate data:cephfs-share ceph-fs:cephfs-share
juju integrate ubuntu:juju-info data:juju-info
```

## 🤝 Project and community

The CephFS client operator is a project of the [Ubuntu High-Performance Computing community](https://ubuntu.com/community/governance/teams/hpc).
It is an open source project that is welcome to community involvement, contributions, suggestions, fixes, and
constructive feedback. Interested in being involved with the development of the CephFS client operator? Check out these links below:

* [Join our online chat](https://matrix.to/#/#ubuntu-hpc:matrix.org)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Code of conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [File a bug report](https://github.com/canonical/cephfs-client-operator/issues)
* [Juju SDK docs](https://juju.is/docs/sdk)

## 📋 License

The CephFS client operator is free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
