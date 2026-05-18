"""Pytest configuration.

Defines the `live` marker for tests that need a real TRACE32 simulator.
Live tests only run when `T32_MCP_LIVE=1` is set, and auto-skip otherwise.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: requires a running TRACE32 simulator (gate with T32_MCP_LIVE=1)",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("T32_MCP_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="set T32_MCP_LIVE=1 to run live TRACE32 tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
