# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manage machine CephFS mounts and dependencies."""

import logging
import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterator, Optional, Union, List

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd

_logger = logging.getLogger(__name__)

CEPH_PATH = pathlib.Path("/etc/ceph")


class Error(Exception):
    """Raise if the CephFS client manager encounters an error."""

    @property
    def name(self):
        """Get a string representation of the error plus class name."""
        return f"<{type(self).__module__}.{type(self).__name__}>"

    @property
    def message(self):
        """Return the message passed as an argument."""
        return self.args[0]

    def __repr__(self):
        """String representation of the error."""
        return f"<{type(self).__module__}.{type(self).__name__} {self.args}>"


@dataclass(frozen=True)
class MountInfo:
    """Mount information.

    Notes:
        See `man fstab` for description of field types.
    """

    endpoint: str
    mountpoint: str
    fstype: str
    options: str
    freq: str
    passno: str


@dataclass(frozen=True)
class CephFSInfo:
    """Information about a shared CephFS."""

    fsid: str
    """Id of the Ceph cluster."""
    name: str
    """Name of the exported CephFS."""
    path: str
    """Exported path of the CephFS."""
    monitor_hosts: [str]
    """Address list of the available Ceph MON nodes."""
    username: str
    """User with authorization to access the filesystem."""
    cephx_key: str
    """Cephx key that authenticates the provided user."""


def supported() -> bool:
    """Check if underlying base supports mounting NFS shares."""
    try:
        result = subprocess.run(
            ["systemd-detect-virt"], stdout=subprocess.PIPE, check=True, text=True
        )
        if "lxc" in result.stdout:
            # Cannot mount NFS shares inside LXD containers.
            return False
        else:
            return True
    except subprocess.CalledProcessError:
        _logger.warning("Could not detect execution in virtualized environment")
        return True


def install() -> None:
    """Install CephFS utilities for mounting CephFS shares.

    Raises:
        Error: Raised if this failed to install any of the required packages.
    """
    _logger.debug("Installing required packages from apt archive.")
    try:
        apt.add_package(["ceph-common", "autofs"], update_cache=True)
    except (apt.PackageError, apt.PackageNotFoundError) as e:
        _logger.error(f"Failed to install required packages. Reason:\n{e.message}")
        raise Error(e.message)


def remove() -> None:
    """Remove CephFS utilities for mounting CephFS shares.

    Raises
        Error: Raised if a required package was installed but could not be removed.
    """
    _logger.debug("Removing required packages from system packages")
    try:
        apt.remove_package(["ceph-common", "autofs"])
    except apt.PackageNotFoundError as e:
        _logger.warning(f"Skipping package that is not installed. Reason:\n{e}")
    except apt.PackageError as e:
        _logger.error(f"Failed to remove required packages. Reason:\n{e}")
        raise Error("Failed to remove required packages")


def fetch(target: str) -> Optional[MountInfo]:
    """Fetch information about a CephFS mount.

    Args:
        target: CephFS share mountpoint information to fetch.

    Returns:
        Optional[MountInfo]: Mount information. None if CephFS share is not mounted.
    """
    # We need to trigger an automount for the mounts that are of type `autofs`,
    # since those could contain a `ceph` mount.
    _trigger_autofs()

    for mount in _mounts("ceph"):
        if mount.mountpoint == target:
            return mount

    return None

def mounts() -> List[MountInfo]:
    """Get all CephFS mounts on a machine.

    Returns:
        List[MountInfo]: All current CephFS mounts on machine.
    """
    _trigger_autofs()

    return list(_mounts("ceph"))

def mounted(target: str) -> bool:
    """Determine if CephFS mountpoint is mounted.

    Args:
        target: mountpoint to check.
    """
    return fetch(target) is not None


def mount(fs_info: CephFSInfo, mountpoint: Union[str, os.PathLike], options: Optional[List[str]] = None) -> None:
    """Mount a CephFS share.

    Args:
        fs_info: Information required to mount the CephFS share.
        mountpoint: System location to mount the CephFS share endpoint.

    Raises:
        Error: Raised if the mount operation fails.
    """
    if options is None:
        options = []
    # Try to create the mountpoint without checking if it exists to avoid TOCTOU.
    target = pathlib.Path(mountpoint)
    try:
        target.mkdir()
        _logger.debug(f"Created mountpoint {mountpoint}.")
    except FileExistsError:
        _logger.warning(f"Mountpoint {mountpoint} already exists.")

    endpoint = f"{fs_info.username}@{fs_info.fsid}.{fs_info.name}={fs_info.path}"
    _logger.debug(f"Mounting CephFS share {endpoint} at {target}")
    autofs_id = _mountpoint_to_autofs_id(target)
    mon_addr = "/".join(fs_info.monitor_hosts)
    mount_opts = ["fstype=ceph", f"mon_addr={mon_addr}", f"secret={fs_info.cephx_key}"] + options
    pathlib.Path(f"/etc/auto.master.d/{autofs_id}.autofs").write_text(
        f"/- /etc/auto.{autofs_id}"
    )
    pathlib.Path(f"/etc/auto.{autofs_id}").write_text(f"{target} -{','.join(mount_opts)} {endpoint}")

    try:
        systemd.service_reload("autofs", restart_on_failure=True)
    except systemd.SystemdError as e:
        _logger.error(f"Failed to mount {endpoint} at {target}. Reason:\n{e}")
        if "Operation not permitted" in str(e) and not supported():
            raise Error("Mounting CephFS shares not supported on LXD containers")
        raise Error(f"Failed to mount {endpoint} at {target}")


def umount(mountpoint: Union[str, os.PathLike]) -> None:
    """Unmount a CephFS share.

    Args:
        mountpoint: CephFS share mountpoint to unmount.

    Raises:
        Error: Raised if CephFS share umount operation fails.
    """
    _logger.debug(f"Unmounting CephFS share at mountpoint {mountpoint}")
    autofs_id = _mountpoint_to_autofs_id(mountpoint)
    pathlib.Path(f"/etc/auto.{autofs_id}").unlink(missing_ok=True)
    pathlib.Path(f"/etc/auto.master.d/{autofs_id}.autofs").unlink(missing_ok=True)

    try:
        systemd.service_reload("autofs", restart_on_failure=True)
    except systemd.SystemdError as e:
        _logger.error(f"Failed to unmount {mountpoint}. Reason:\n{e}")
        raise Error(f"Failed to unmount {mountpoint}")

    shutil.rmtree(mountpoint, ignore_errors=True)


def _trigger_autofs() -> None:
    """Triggers a mount on all filesystems handled by autofs.

    This function is useful to make autofs-managed mounts appear on the
    `/proc/mount` file, since they could be unmounted when reading the file.
    """
    for fs in _mounts("autofs"):
        _logger.info(f"triggering automount for `{fs.mountpoint}`")
        try:
            os.scandir(fs.mountpoint).close()
        except OSError as e:
            # Not critical since it could also be caused by unrelated mounts,
            # but should be good to log it in case this causes problems.
            _logger.warning(f"Could not trigger automount for `{fs.mountpoint}`. Reason:\n{e}")


def _mountpoint_to_autofs_id(mountpoint: Union[str, os.PathLike]) -> str:
    """Get the autofs id of a mountpoint path.

    Args:
        mountpoint: NFS share mountpoint.
    """
    path = pathlib.Path(mountpoint).resolve()
    return str(path).lstrip("/").replace("/", "-")


def _mounts(fstype: str) -> Iterator[MountInfo]:
    """Gets an iterator of all mounts in the system that have the requested fstype.

    Returns:
        Iterator[MountInfo]: All the mounts with a valid fstype.
    """
    with pathlib.Path("/proc/mounts").open("rt") as mounts:
        for mount in mounts:
            # Lines in /proc/mounts follow the standard format
            # <endpoint> <mountpoint> <fstype> <options> <freq> <passno>
            m = MountInfo(*mount.split())
            if not m.fstype.startswith(fstype):
                continue

            yield m
