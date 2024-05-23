#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import juju
import pytest
import tenacity
import yaml
from helpers import bootstrap_microceph
from pylxd import Client
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
BASE = "ubuntu"
CLIENT = METADATA["name"]
PROXY = "cephfs-server-proxy"


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
@pytest.mark.order(1)
async def test_build_and_deploy(ops_test: OpsTest, charm_base: str) -> None:
    """Test that the CephFS client can be built and deployed."""
    share_info, auth_info = bootstrap_microceph()

    charm = str(await ops_test.build_charm(".", verbosity="debug"))
    logger.info(f"Deploying {PROXY} against {CLIENT} and {BASE}")

    # Deploy the charms and wait for active/idle status
    await asyncio.gather(
        ops_test.model.deploy(
            PROXY,
            application_name=PROXY,
            config={
                "fsid": share_info.fsid,
                "sharepoint": f"{share_info.name}:{share_info.path}",
                "monitor-hosts": " ".join(share_info.monitor_hosts),
                "auth-info": f"{auth_info.username}:{auth_info.key}",
            },
            channel="edge",
            num_units=1,
            base="ubuntu@22.04",
        ),
        ops_test.model.deploy(
            BASE,
            application_name=BASE,
            channel="edge",
            num_units=1,
            base=charm_base,
            constraints=juju.constraints.parse("virt-type=virtual-machine"),
        ),
        ops_test.model.deploy(
            charm,
            application_name=CLIENT,
            config={"mountpoint": "/data"},
            num_units=0,
            base=charm_base,
        ),
    )

    # Set integrations for charmed applications
    await ops_test.model.integrate(f"{CLIENT}:juju-info", f"{BASE}:juju-info")
    await ops_test.model.integrate(f"{CLIENT}:cephfs-share", f"{PROXY}:cephfs-share")

    # Reduce the update status frequency to accelerate the triggering of deferred events.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PROXY, BASE, CLIENT], status="active", raise_on_blocked=True, timeout=1000
        )


@pytest.mark.abort_on_fail
@pytest.mark.order(2)
@tenacity.retry(
    wait=tenacity.wait.wait_exponential(multiplier=2, min=1, max=30),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def test_share_active(ops_test: OpsTest) -> None:
    """Test that the CephFS share is mounted on the principle base charm."""
    logger.info(f"Checking that /data is mounted on principle charm {BASE}")
    base_unit = ops_test.model.applications[BASE].units[0]
    result = (await base_unit.ssh("ls /data")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result


@pytest.mark.abort_on_fail
@pytest.mark.order(3)
async def test_automount_on_reboot(ops_test: OpsTest) -> None:
    """Test that the CephFS share is automatically remounted after a server reboot."""
    base_unit = ops_test.model.applications[BASE].units[0]
    instance_id = base_unit.machine.safe_data["instance-id"]

    logger.info(f"Restarting machine {instance_id} for principle charm {BASE}")
    client = Client()
    instance = client.instances.get(instance_id)
    instance.restart(force=False, wait=True)

    # App does NOT immediately become active after an instance restart.
    # We need to let the app stabilize itself.
    await ops_test.model.wait_for_idle(apps=[BASE], status="active", timeout=1000)

    logger.info(f"Checking that /data has been remounted on principle charm {BASE}")
    result = (await base_unit.ssh("ls /data")).strip("\n")
    assert "test-1" in result
    assert "test-2" in result
    assert "test-3" in result


@pytest.mark.abort_on_fail
@pytest.mark.order(4)
async def test_reintegrate(ops_test: OpsTest) -> None:
    """Test that the client can reintegrate with the server."""
    await ops_test.model.applications[CLIENT].destroy_relation(
        "cephfs-share", f"{PROXY}:cephfs-share", block_until_done=True
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PROXY], status="active", raise_on_blocked=True, timeout=1000
        )
        await ops_test.model.wait_for_idle(
            apps=[CLIENT], status="waiting", raise_on_error=True, timeout=1000
        )

    await ops_test.model.integrate(f"{CLIENT}:cephfs-share", f"{PROXY}:cephfs-share")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[PROXY, CLIENT], status="active", raise_on_blocked=True, timeout=360
        )
