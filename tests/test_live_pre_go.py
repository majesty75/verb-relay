"""Tier 2: live TRACE32 simulator tests that stay BEFORE the first Go/Step.

The free TRACE32 simulator allows unlimited script commands until you issue
`Go`/`Step` — only after that does the 50-command-budget kick in. So we can
exercise the entire setup + inspection surface here without hitting the cap.

Gate: T32_MCP_LIVE=1. Auto-skip otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _find_demo_axf() -> Path | None:
    """Look for any AXF/ELF Lauterbach ships with TRACE32."""
    roots = []
    env = os.environ.get("T32_DEMO_AXF")
    if env:
        return Path(env)
    for env_root in ("T32SYS", "T32_HOME"):
        if env_root in os.environ:
            roots.append(Path(os.environ[env_root]))
    roots.extend([Path("/Applications/t32"), Path.home() / "t32", Path("/opt/t32")])
    for r in roots:
        if not r.exists():
            continue
        # Common demo paths for ARM
        for sub in ("demo/arm/compiler/arm", "demo/arm/compiler/gcc", "demo/arm"):
            for ext in ("*.axf", "*.elf"):
                hits = list((r / sub).rglob(ext)) if (r / sub).exists() else []
                if hits:
                    return hits[0]
    return None


@pytest.fixture(scope="module")
def sim_instance():
    """Spawn one cortex-m simulator for the whole module."""
    from trace32_mcp.tools.instances import t32_spawn, t32_shutdown
    out = t32_spawn({"arch": "cortexm"})
    assert out["ok"], f"failed to spawn sim: {out}"
    node = out["instance"]["node_name"]
    yield node
    t32_shutdown({"node_name": node, "force": True})


def test_healthcheck_passes(sim_instance):
    from trace32_mcp.tools.healthcheck import t32_healthcheck
    out = t32_healthcheck({"node_name": sim_instance})
    assert out["ok"], f"healthcheck failed: {out}"


def test_status_reports_cpu(sim_instance):
    from trace32_mcp.tools.session import t32_status
    out = t32_status({"node_name": sim_instance})
    assert out["ok"]
    assert "cpu" in out["target"]


def test_load_program_if_demo_axf_available(sim_instance):
    axf = _find_demo_axf()
    if axf is None:
        pytest.skip("no Lauterbach demo AXF found; set T32_DEMO_AXF or install demos")
    from trace32_mcp.tools.program import t32_load_program
    out = t32_load_program({"node_name": sim_instance, "axf_path": str(axf)})
    assert out["ok"], f"load failed: {out}"


def test_list_symbols_after_load(sim_instance):
    if _find_demo_axf() is None:
        pytest.skip("no demo AXF")
    from trace32_mcp.tools.symbols import t32_list_symbols
    out = t32_list_symbols({"node_name": sim_instance, "pattern": "main*", "limit": 50})
    assert out["ok"]


def test_breakpoint_set_then_list(sim_instance):
    if _find_demo_axf() is None:
        pytest.skip("no demo AXF")
    from trace32_mcp.tools.control import t32_breakpoint
    set_out = t32_breakpoint({"node_name": sim_instance, "action": "set", "location": "main"})
    assert set_out["ok"], f"set failed: {set_out}"
    list_out = t32_breakpoint({"node_name": sim_instance, "action": "list"})
    assert list_out["ok"]


def test_eval_symbol(sim_instance):
    if _find_demo_axf() is None:
        pytest.skip("no demo AXF")
    from trace32_mcp.tools.inspect import t32_eval
    out = t32_eval({"node_name": sim_instance, "expression": "sYmbol.ADDRESS(main)"})
    assert out["ok"]


def test_get_log_returns_area(sim_instance):
    from trace32_mcp.tools.script import t32_run_command
    from trace32_mcp.tools.instances import t32_get_log
    t32_run_command({"node_name": sim_instance, "line": 'PRINT "live-test-marker"'})
    log = t32_get_log({"node_name": sim_instance, "source": "area"})
    assert log["ok"]
    assert "live-test-marker" in log["sources"].get("area_MCPLOG", "")
