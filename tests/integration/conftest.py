#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configure integration test run."""

import pytest
from _pytest.config.argparsing import Parser


def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--charm-base", action="store", default="ubuntu@22.04", help="Charm base to test."
    )


@pytest.fixture(scope="module")
def charm_base(request) -> str:
    """Get cephfs-client charm series to use."""
    return request.config.getoption("--charm-base")
