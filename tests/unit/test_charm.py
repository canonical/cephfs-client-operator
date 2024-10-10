#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test base charm events such as Install, Stop, etc."""

import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

import yaml
from ops.model import BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness

import utils.manager as cephfs
from charm import PEER_NAME, CephFSClientCharm

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


class TestCharm(unittest.TestCase):
    """Test cephfs-client charmed operator."""

    def setUp(self) -> None:
        self.harness = Harness(CephFSClientCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.add_relation(PEER_NAME, APP_NAME)
        self.harness.set_leader(True)
        self.harness.begin()

    @patch("utils.manager.install")
    def test_install(self, _) -> None:
        """Test that cephfs-client can successfully be installed."""
        self.harness.charm.on.install.emit()
        self.assertEqual(
            self.harness.model.unit.status, MaintenanceStatus("Installing required packages")
        )

    @patch("utils.manager.install", side_effect=cephfs.Error("Failed to install `ceph-common`"))
    def test_install_fail(self, _) -> None:
        """Test that cephfs-client install fail handler works."""
        self.harness.charm.on.install.emit()
        self.assertEqual(
            self.harness.model.unit.status, BlockedStatus("Failed to install `ceph-common`")
        )

    @patch("utils.manager.install")
    def test_upgrade_charm(self, _) -> None:
        """Test that cephfs-client installs packages after upgrade."""
        self.harness.charm.on.upgrade_charm.emit()
        self.assertEqual(
            self.harness.model.unit.status, MaintenanceStatus("Installing required packages")
        )

    @patch("utils.manager.install", side_effect=cephfs.Error("Failed to install `ceph-common`"))
    def test_upgrade_charm_fail(self, _) -> None:
        """Test that cephfs-client install fail handler on upgrade works."""
        self.harness.charm.on.upgrade_charm.emit()
        self.assertEqual(
            self.harness.model.unit.status, BlockedStatus("Failed to install `ceph-common`")
        )

    @patch(
        "charm.CephFSClientCharm.config",
        new_callable=PropertyMock(return_value={"mountpoint": None}),
    )
    def test_config_no_mountpoint(self, _) -> None:
        """Test config changed handler when no mountpoint is set."""
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, BlockedStatus("No configured mountpoint"))

    @patch(
        "charm.CephFSClientCharm.config",
        new_callable=PropertyMock(return_value={"mountpoint": "/data"}),
    )
    def test_config_set_mountpoint(self, _) -> None:
        """Test config changed handler when new mountpoint is available."""
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))

    @patch(
        "charm.CephFSClientCharm.config",
        new_callable=PropertyMock(
            return_value={
                "mountpoint": "/nodata",
                "noexec": True,
                "nosuid": True,
                "nodev": True,
                "read-only": True,
            }
        ),
    )
    def test_config_all_set(self, _) -> None:
        """Test config changed handler when config is available."""
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.charm.get_state("config"),
            {
                "mountpoint": "/nodata",
                "noexec": True,
                "nosuid": True,
                "nodev": True,
                "read-only": True,
            },
        )

    @patch(
        "charm.CephFSClientCharm.config",
        new_callable=PropertyMock(
            return_value={
                "mountpoint": "/nodata",
                "noexec": True,
                "nosuid": True,
                "nodev": True,
                "read-only": True,
            }
        ),
    )
    def test_config_already_set(self, *_) -> None:
        """Test config is frozen after all values have been set."""
        # Patch charm stored state.
        self.harness.charm.set_state(
            "config",
            {
                "mountpoint": "/data",
                "noexec": False,
                "nosuid": False,
                "nodev": False,
                "read-only": False,
            },
        )
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))
        self.assertEqual(
            self.harness.charm.get_state("config"),
            {
                "mountpoint": "/data",
                "noexec": False,
                "nosuid": False,
                "nodev": False,
                "read-only": False,
            },
        )

    @patch("utils.manager.mounted", return_value=True)
    @patch.object(cephfs, "umount")
    @patch("utils.manager.mounts", return_value=[])
    @patch.object(cephfs, "remove")
    def test_on_stop(self, remove, mounts, umount, mounted) -> None:
        """Test on stop handler."""
        self.harness.charm.on.stop.emit()
        umount.assert_called_once()
        remove.assert_called_once()
        self.assertEqual(self.harness.model.unit.status, MaintenanceStatus("Shutting down..."))
