#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Test cephfs-share integration."""

import unittest
from pathlib import Path
from unittest.mock import patch

import utils.manager as cephfs
import yaml
from charm import PEER_NAME, CephFSClientCharm
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


class TestCephFSShare(unittest.TestCase):
    """Test cephfs-share integration."""

    def setUp(self) -> None:
        self.harness = Harness(CephFSClientCharm)
        self.integration_id = self.harness.add_relation("cephfs-share", "cephfs-server-proxy")
        self.harness.add_relation(PEER_NAME, APP_NAME)
        self.harness.add_relation_unit(self.integration_id, "cephfs-server-proxy/0")
        self.harness.set_leader(True)
        self.harness.begin()
        # Patch charm stored state.
        self.harness.charm.set_state(
            "config",
            {
                "mountpoint": "/data",
            },
        )

    def test_server_connected_no_mountpoint(self) -> None:
        """Test server connected handler when there is no configured mountpoint."""
        # Patch charm stored state.
        self.harness.charm.set_state("config", {})
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.server_connected.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, BlockedStatus("No configured mountpoint"))

    def test_server_connected(self) -> None:
        """Test server connected handler when mountpoint is configured."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.server_connected.emit(integration, app)

    @patch("utils.manager.mount", side_effect=cephfs.Error("Failed to mount share"))
    @patch("utils.manager.mounted", return_value=False)
    def test_mount_share_failed(self, *_) -> None:
        """Test mount share handler when mount fails."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.mount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, BlockedStatus("Failed to mount share"))

    @patch("utils.manager.mounted", return_value=True)
    def test_mount_share_already_mounted(self, _) -> None:
        """Test mount share handler when CephFS share is already mounted."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.mount_share.emit(integration, app)

    @patch("utils.manager.mount")
    @patch("utils.manager.mounted", return_value=False)
    def test_mount_share(self, *_) -> None:
        """Test mount share handler."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.mount_share.emit(integration, app)
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)

    @patch("utils.manager.umount", side_effect=cephfs.Error("Failed to umount share"))
    @patch("utils.manager.mounted")
    def test_umount_share_failed(self, *_) -> None:
        """Test umount share handler when umount fails."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.umount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, BlockedStatus("Failed to umount share"))

    @patch("utils.manager.umount")
    @patch(
        "utils.manager.fetch",
        return_value=cephfs.MountInfo(
            endpoint="10.143.60.15:/",
            mountpoint="/data",
            fstype="ceph",
            options="some,thing",
            freq="0",
            passno="0",
        ),
    )
    def test_umount_share_endpoint_provided_and_mounted(self, *_) -> None:
        """Test umount share handler with endpoint and active mount."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.update_relation_data(
            self.integration_id,
            "cephfs-server-proxy",
            {
                "share_info": """{
                    "fsid": "ce87f9f4-2f46-4bc1-94b0-12e5f1698dcc",
                    "name": "ceph-fs",
                    "path": "/",
                    "monitor_hosts": ["10.143.60.15"]}
                """,
            },
        )
        self.harness.charm._cephfs_share.on.umount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))

    @patch("utils.manager.fetch", return_value=None)
    @patch("utils.manager.mount")
    def test_umount_share_endpoint_provided_not_mounted(self, *_) -> None:
        """Test umount share handler with endpoint and no mount."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.update_relation_data(
            self.integration_id,
            "cephfs-server-proxy",
            {
                "share_info": """{
                    "fsid": "ce87f9f4-2f46-4bc1-94b0-12e5f1698dcc",
                    "name": "ceph-fs",
                    "path": "/",
                    "monitor_hosts": ["10.143.60.15"]}
                """,
            },
        )
        self.harness.charm._cephfs_share.on.umount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))

    @patch("utils.manager.umount")
    @patch("utils.manager.mounted", return_value=True)
    def test_umount_share_no_endpoint_and_mounted(self, *_) -> None:
        """Test umount share handler with no endpoint and active mount."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.umount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))

    @patch("utils.manager.mounted", return_value=False)
    def test_umount_share_no_endpoint_not_mounted(self, _) -> None:
        """Test umount share handler with no endpoint and no mount."""
        integration = self.harness.charm.model.get_relation("cephfs-share", self.integration_id)
        app = self.harness.charm.model.get_app("cephfs-server-proxy")
        self.harness.charm._cephfs_share.on.umount_share.emit(integration, app)
        self.assertEqual(self.harness.model.unit.status, WaitingStatus("Waiting for CephFS share"))
