#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
import utils.manager as cephfs
from charms.storage_libs.v0.ceph_interfaces import (
    CephFSRequires,
    MountShareEvent,
    ServerConnectedEvent,
    UmountShareEvent,
)

logger = logging.getLogger(__name__)


class CephFSClientOperatorCharm(ops.CharmBase):
    """Charm the application."""

    _stored = ops.StoredState()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # TODO: Add mount opts
        self._stored.set_default(mountpoint=None)

        self._ceph_share = CephFSRequires(self, "cephfs-share")

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self._ceph_share.on.server_connected, self._on_server_connected)
        self.framework.observe(self._ceph_share.on.mount_share, self._on_mount_share)
        self.framework.observe(self._ceph_share.on.umount_share, self._on_umount_share)

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

        if self._stored.mountpoint:
            logger.warning(f"Mountpoint can only be set once. Ignoring {mountpoint}")
        else:
            logger.debug(f"Setting mountpoint as {mountpoint}")
            self._stored.mountpoint = mountpoint

        self.unit.status = ops.WaitingStatus("Waiting for CephFS share")

    def _on_stop(self, _) -> None:
        """Clean up machine before de-provisioning."""
        if cephfs.mounted(mountpoint := self.config.get("mountpoint")):
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
        if not self._stored.mountpoint:
            logger.warning("Deferring ServerConnectedEvent event because mountpoint is not set")
            self.unit.status = ops.BlockedStatus("No configured mountpoint")
            event.defer()
            return

        self._ceph_share.request_share(event.relation.id, name=self._stored.mountpoint)

    def _on_mount_share(self, event: MountShareEvent) -> None:
        """Mount a CephFS share."""
        try:
            if not cephfs.mounted(self._stored.mountpoint):
                share_info = event.share_info
                auth_info = self.model.get_secret(id=share_info.auth_id).get_content()

                cephfs.mount(
                    cephfs.CephFSInfo(
                        fsid=share_info.fsid,
                        name=share_info.name,
                        path=share_info.path,
                        monitor_hosts=share_info.monitor_hosts,
                        username=auth_info["username"],
                        cephx_key=auth_info["cephx-key"],
                    ),
                    self._stored.mountpoint,
                )
                self.unit.status = ops.ActiveStatus(
                    f"CephFS share mounted at {self._stored.mountpoint}"
                )
            else:
                logger.warning(f"Mountpoint {self._stored.mountpoint} already mounted")
        except cephfs.Error as e:
            self.unit.status = ops.BlockedStatus(e.message)

    def _on_umount_share(self, event: UmountShareEvent) -> None:
        """Umount a CephFS share."""
        self.unit.status = ops.MaintenanceStatus(
            f"Unmounting CephFS share at {self._stored.mountpoint}"
        )
        try:
            if cephfs.mounted(self._stored.mountpoint):
                cephfs.umount(self._stored.mountpoint)
            else:
                logger.warning(f"{self._stored.mountpoint} is not mounted")

            self.unit.status = ops.WaitingStatus("Waiting for CephFS share")
        except cephfs.Error as e:
            self.unit.status = ops.BlockedStatus(e.message)


if __name__ == "__main__":  # pragma: nocover
    ops.main(CephFSClientOperatorCharm)  # type: ignore
