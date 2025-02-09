#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""CephFS client charmed operator for mounting CephFS shares."""

import json
import logging
from typing import Any

import ops
from charms.storage_libs.v0.cephfs_interfaces import (
    CephFSRequires,
    MountShareEvent,
    ServerConnectedEvent,
    UmountShareEvent,
)

import utils.manager as cephfs

logger = logging.getLogger(__name__)

PEER_NAME = "peers"
MOUNT_OPTS = ["noexec", "nosuid", "nodev", "read-only"]


class CephFSClientCharm(ops.CharmBase):
    """CephFS client charmed operator."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._cephfs_share = CephFSRequires(self, "cephfs-share")

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self._cephfs_share.on.server_connected, self._on_server_connected)
        self.framework.observe(self._cephfs_share.on.mount_share, self._on_mount_share)
        self.framework.observe(self._cephfs_share.on.umount_share, self._on_umount_share)

        # ensures the required packages are installed after a `juju refresh`.
        self.framework.observe(self.on.upgrade_charm, self._on_install)

    def _on_install(self, _) -> None:
        """Install required packages for mounting CephFS shares."""
        self.unit.status = ops.MaintenanceStatus("Installing required packages")
        try:
            cephfs.install()
        except cephfs.Error as e:
            self.unit.status = ops.BlockedStatus(e.message)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle updates to CephFS client configuration."""
        mountpoint = self.config.get("mountpoint")
        if not mountpoint:
            self.unit.status = ops.BlockedStatus("No configured mountpoint")
            return

        config = self.get_state("config")

        if config.get("mountpoint"):
            logger.warning(f"Mountpoint can only be set once. Ignoring {mountpoint}")
        else:
            logger.debug(f"Setting mountpoint as {mountpoint}")
            config["mountpoint"] = mountpoint

        for opt in MOUNT_OPTS:
            val = config.get(opt)
            new_val = self.config.get(opt)
            if val is None:
                config[opt] = new_val
            else:
                logger.warning(f"{opt} can only be set once. Ignoring {new_val}.")

        self.set_state("config", config)

        self.unit.status = ops.WaitingStatus("Waiting for CephFS share")

    def _on_stop(self, _) -> None:
        """Clean up machine before de-provisioning."""
        if cephfs.mounted(mountpoint := self.get_state("config").get("mountpoint", "")):
            self.unit.status = ops.MaintenanceStatus(f"Unmounting {mountpoint}")
            cephfs.umount(mountpoint)

        # Only remove the required packages if there are no existing CephFS shares outside of charm.
        if not cephfs.mounts():
            self.unit.status = ops.MaintenanceStatus("Removing required packages")
            cephfs.remove()

        self.unit.status = ops.MaintenanceStatus("Shutting down...")

    def _on_server_connected(self, event: ServerConnectedEvent) -> None:
        """Handle when client has connected to CephFS server."""
        self.unit.status = ops.MaintenanceStatus("Requesting CephFS share")
        mountpoint = self.get_state("config").get("mountpoint")
        if not mountpoint:
            logger.warning("Deferring ServerConnectedEvent event because mountpoint is not set")
            self.unit.status = ops.BlockedStatus("No configured mountpoint")
            event.defer()
            return

        self._cephfs_share.request_share(event.relation.id, name=mountpoint)

    def _on_mount_share(self, event: MountShareEvent) -> None:
        """Mount a CephFS share."""
        config = self.get_state("config")
        try:
            mountpoint = config["mountpoint"]
            if not cephfs.mounted(mountpoint):
                opts = []
                opts.append("noexec" if config.get("noexec") else "exec")
                opts.append("nosuid" if config.get("nosuid") else "suid")
                opts.append("nodev" if config.get("nodev") else "dev")
                opts.append("ro" if config.get("read-only") else "rw")

                cephfs.mount(event.share_info, event.auth_info, mountpoint, options=opts)
                self.unit.status = ops.ActiveStatus(f"CephFS share mounted at {mountpoint}")
            else:
                logger.warning(f"Mountpoint {mountpoint} already mounted")
        except cephfs.Error as e:
            self.unit.status = ops.BlockedStatus(e.message)

    def _on_umount_share(self, event: UmountShareEvent) -> None:
        """Umount a CephFS share."""
        mountpoint = self.get_state("config")["mountpoint"]

        self.unit.status = ops.MaintenanceStatus(f"Unmounting CephFS share at {mountpoint}")
        try:
            if cephfs.mounted(mountpoint):
                cephfs.umount(mountpoint)
            else:
                logger.warning(f"{mountpoint} is not mounted")

            self.unit.status = ops.WaitingStatus("Waiting for CephFS share")
        except cephfs.Error as e:
            self.unit.status = ops.BlockedStatus(e.message)

    @property
    def peers(self):
        """Fetch the peer relation."""
        return self.model.get_relation(PEER_NAME)

    def set_state(self, key: str, data: Any) -> None:
        """Insert a value into the global state."""
        self.peers.data[self.app][key] = json.dumps(data)

    def get_state(self, key: str) -> dict[Any, Any]:
        """Get a value from the global state."""
        if not self.peers:
            return {}

        data = self.peers.data[self.app].get(key, "{}")
        return json.loads(data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSClientCharm)  # type: ignore
