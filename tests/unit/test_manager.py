#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test cephfs manager utils."""

import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace
from typing import List, Optional, Union
from unittest.mock import patch

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
import utils.manager as cephfs
from charms.storage_libs.v0.cephfs_interfaces import CephFSAuthInfo, CephFSShareInfo
from pyfakefs.fake_filesystem_unittest import TestCase

SHARE_INFO = CephFSShareInfo(
    fsid="abcdefg",
    name="ceph-fs",
    path="/",
    monitor_hosts=["10.143.60.15", "10.143.60.16"],
)
AUTH_INFO = CephFSAuthInfo(
    username="ceph-fs-client",
    key="key",
)


@dataclass(frozen=True, kw_only=True)
class MountParams:
    """Test parameters for the `mount` operation."""

    share_info: CephFSShareInfo
    auth_info: CephFSAuthInfo
    mountpoint: Union[str, os.PathLike]
    options: Optional[List[str]]
    master_file: Union[str, os.PathLike]
    master_data: str
    map_file: Union[str, os.PathLike]
    map_data: str


@patch("charms.operator_libs_linux.v1.systemd.service_reload")
@patch("subprocess.run")
class TestCephFSManager(TestCase):
    """Test cephfs manager utils."""

    def setUp(self) -> None:
        self.setUpPyfakefs()
        self.fs.create_dir("/etc/auto.master.d")
        self.fs.create_file(
            "/proc/mounts",
            contents=textwrap.dedent(
                """\
                /dev/sda1 / ext4 rw,relatime,discard,errors=remount-ro 0 0
                10.143.60.15:/ /data ceph rw,relatime,info 0 0
                [ffcc:aabb::10]:/ /things ceph rw,relatime,info 0 0
                nsfs /run/snapd/ns/lxd.mnt nsfs rw 0 0
                /etc/auto.data /data autofs rw,relatime 0 0
                tmpfs /run/lock tmpfs rw,nosuid,nodev,noexec,relatime 0 0
                """,
            ),
        )
        self.data_mount = cephfs.MountInfo(
            "10.143.60.15:/", "/data", "ceph", "rw,relatime,info", "0", "0"
        )
        self.things_mount = cephfs.MountInfo(
            "[ffcc:aabb::10]:/", "/things", "ceph", "rw,relatime,info", "0", "0"
        )

    def test_mount_valid_endpoint(self, *_) -> None:
        """Test that various kinds of endpoints can be properly mounted."""
        cases = [
            # Common
            MountParams(
                share_info=SHARE_INFO,
                auth_info=AUTH_INFO,
                mountpoint="/things",
                options=[],
                master_file="/etc/auto.master.d/things.autofs",
                master_data="/- /etc/auto.things",
                map_file="/etc/auto.things",
                map_data=(
                    "/things "
                    "-fstype=ceph,mon_addr=10.143.60.15/10.143.60.16,secret=key "
                    "ceph-fs-client@abcdefg.ceph-fs=/"
                ),
            ),
            # Common + options
            MountParams(
                share_info=SHARE_INFO,
                auth_info=AUTH_INFO,
                mountpoint="/data",
                options=["some", "opts"],
                master_file="/etc/auto.master.d/data.autofs",
                master_data="/- /etc/auto.data",
                map_file="/etc/auto.data",
                map_data=(
                    "/data "
                    "-fstype=ceph,mon_addr=10.143.60.15/10.143.60.16,secret=key,some,opts "
                    "ceph-fs-client@abcdefg.ceph-fs=/"
                ),
            ),
        ]

        for case in cases:
            with self.subTest(
                share_info=case.share_info,
                auth_info=case.auth_info,
                mountpoint=case.mountpoint,
                options=case.options,
            ):
                cephfs.mount(case.share_info, case.auth_info, case.mountpoint, case.options)

                master_data = Path(case.master_file).read_text()
                self.assertEqual(case.master_data, master_data)
                map_data = Path(case.map_file).read_text()
                self.assertEqual(case.map_data, map_data)

    def test_mount_systemd_error(self, subproc, reload, *_):
        """Test that the mount operation correctly raises if systemd cannot reload the service."""
        subproc.return_value = SimpleNamespace(stdout="kvm")

        # Normal error
        reload.side_effect = systemd.SystemdError("error message")
        with self.assertRaises(cephfs.Error) as sup:
            cephfs.mount(SHARE_INFO, AUTH_INFO, "/data")
        self.assertEqual(
            sup.exception.message,
            "Failed to mount ceph-fs-client@abcdefg.ceph-fs=/ at /data",
        )

        # Operation not permitted but not LXC virtualization
        reload.side_effect = systemd.SystemdError("Operation not permitted")
        with self.assertRaises(cephfs.Error) as sup:
            cephfs.mount(SHARE_INFO, AUTH_INFO, "/data")
        self.assertEqual(
            sup.exception.message,
            "Failed to mount ceph-fs-client@abcdefg.ceph-fs=/ at /data",
        )

        subproc.return_value = SimpleNamespace(stdout="lxc")

        # Normal error on LXC virtualization
        reload.side_effect = systemd.SystemdError("error message")
        with self.assertRaises(cephfs.Error) as sup:
            cephfs.mount(SHARE_INFO, AUTH_INFO, "/data")
        self.assertEqual(
            sup.exception.message,
            "Failed to mount ceph-fs-client@abcdefg.ceph-fs=/ at /data",
        )

        # Operation not permitted on LXC virtualization. Should show a useful error message.
        reload.side_effect = systemd.SystemdError("Operation not permitted")
        with self.assertRaises(cephfs.Error) as sup:
            cephfs.mount(SHARE_INFO, AUTH_INFO, "/data")
        self.assertEqual(
            sup.exception.message, "Mounting CephFS shares not supported on LXD containers"
        )

        # Error trying to check the virtualization type. Should throw the normal error message
        # for good measure.
        subproc.side_effect = CalledProcessError(-1, "error message")
        with self.assertRaises(cephfs.Error) as sup:
            cephfs.mount(SHARE_INFO, AUTH_INFO, "/data")
        self.assertEqual(
            sup.exception.message,
            "Failed to mount ceph-fs-client@abcdefg.ceph-fs=/ at /data",
        )

    @patch("charms.operator_libs_linux.v0.apt.add_package")
    def test_install(self, add_package, *_):
        """Test that the install operation correctly succeeds or bails on error."""
        cephfs.install()

        add_package.side_effect = apt.PackageError("error message")
        with self.assertRaises(cephfs.Error) as e:
            cephfs.install()

        self.assertEqual(e.exception.message, "error message")

    @patch("charms.operator_libs_linux.v0.apt.remove_package")
    def test_remove(self, remove_package, *_):
        """Test that the remove operation never bails on error, but fails on package error."""
        # Sunny day
        cephfs.remove()

        remove_package.side_effect = apt.PackageNotFoundError("error message")
        # Rainy day
        cephfs.remove()

        remove_package.side_effect = apt.PackageError("error message")
        with self.assertRaises(cephfs.Error):
            cephfs.remove()

    def test_fetch_valid(self, *_):
        """Test that the fetch operation fetches all defined ceph mounts."""
        cases = [
            ("/data", self.data_mount),
            ("/things", self.things_mount),
        ]

        for case, info in cases:
            with self.subTest(target=case):
                self.assertEqual(cephfs.fetch(case), info)

    def test_fetch_invalid(self, *_):
        """Test that the fetch operation cannot fetch unknown or invalid mounts."""
        cases = ["/dev/sda1", "/", "/datum", "/etc/auto.data"]

        for case in cases:
            with self.subTest(target=case):
                self.assertIsNone(cephfs.fetch(case))

    def test_mounts(self, *_):
        """Test that the mounts operation returns only ceph mounts."""
        self.assertEqual(cephfs.mounts(), [self.data_mount, self.things_mount])

    def test_umount(self, _subprocess, reload, *_):
        """Test that the umount operation correctly deletes files and raises if systemd raises."""
        self.fs.create_dir("/data")
        self.fs.create_file("/etc/auto.data")
        self.fs.create_file("/etc/auto.master.d/data.autofs")

        cephfs.umount("/data")

        self.assertFalse(self.fs.exists("/data"))
        self.assertFalse(self.fs.exists("/etc/auto.data"))
        self.assertFalse(self.fs.exists("/etc/auto.master.d/data.autofs"))

        reload.side_effect = systemd.SystemdError("error message")
        with self.assertRaises(cephfs.Error) as e:
            # umount cannot throw if the files don't exist, only if systemd raises an error.
            cephfs.umount("/data")

        self.assertEqual(e.exception.message, "Failed to unmount /data")

    def test_error(self, *_):
        """Test the properties of the Error class."""
        error = cephfs.Error("error message")
        self.assertEqual(error.name, "<utils.manager.Error>")
        self.assertEqual(repr(error), "<utils.manager.Error ('error message',)>")
