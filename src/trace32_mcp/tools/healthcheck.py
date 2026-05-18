"""t32_healthcheck — verify a registered T32 instance is actually responsive.

Different from t32_status (which reports debugger state). Healthcheck answers:
  "If I send a command right now, will TRACE32 receive and execute it?"
"""

from __future__ import annotations

import time

from pydantic import Field

from ..session import get_client
from ..t32_process import is_port_open, registry
from ._common import TargetSelector


class HealthcheckInput(TargetSelector):
    """Pick the instance via node_name / host+port / default-to-most-recent."""

    probe: str = Field(
        default="PRINT \"trace32-mcp-ping\"",
        description="PRACTICE command to echo back during the readiness probe.",
    )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def t32_healthcheck(args: dict) -> dict:
    p = HealthcheckInput(**args)
    reg = registry()

    # Resolve the target instance manually (so we can report each step
    # even when the target is partially broken).
    if p.node_name:
        inst = reg.get_by_node(p.node_name)
    elif p.host and p.port:
        inst = reg.get_by_endpoint(p.host, p.port)
    else:
        lst = reg.list()
        inst = lst[-1] if lst else None

    if inst is None:
        return {
            "ok": False,
            "error": "no matching T32 instance is registered. Call t32_spawn or t32_attach first.",
            "registered": [i.to_dict() for i in reg.list()],
        }

    checks: list[dict] = []

    # 1. TCP reachability
    t0 = time.perf_counter()
    tcp_ok = is_port_open(inst.host, inst.port, timeout=1.0)
    checks.append({
        "name": "tcp_port_open",
        "ok": tcp_ok,
        "latency_ms": _ms(t0),
        "detail": f"{inst.host}:{inst.port}",
    })
    if not tcp_ok:
        return {"ok": False, "instance": inst.to_dict(), "checks": checks,
                "error": f"nothing listening at {inst.host}:{inst.port}"}

    # 2. RCL handshake (Init + Attach happens lazily on first command)
    try:
        client = get_client(host=inst.host, port=inst.port, node_name=inst.node_name)
    except Exception as e:
        checks.append({"name": "rcl_client_init", "ok": False, "error": str(e)})
        return {"ok": False, "instance": inst.to_dict(), "checks": checks}

    # 3. State query (T32_GetState) — works as soon as Attach succeeds
    t0 = time.perf_counter()
    try:
        st = client.state()
        checks.append({
            "name": "state_query",
            "ok": True,
            "latency_ms": _ms(t0),
            "state": st["state"],
            "cpu": st.get("cpu", ""),
        })
    except Exception as e:
        checks.append({"name": "state_query", "ok": False, "latency_ms": _ms(t0), "error": str(e)})
        return {"ok": False, "instance": inst.to_dict(), "checks": checks}

    # 4. Trivial command echo (PRINT)
    t0 = time.perf_counter()
    try:
        res = client.run(p.probe).to_dict()
        echo_ok = res.get("ok", False) and "trace32-mcp" in (res.get("text") or "")
        checks.append({
            "name": "echo_command",
            "ok": echo_ok,
            "latency_ms": _ms(t0),
            "cmd": p.probe,
            "text": (res.get("text") or "")[:200],
            "mode_flags": res.get("mode_flags", []),
            "practice_state": res.get("practice_state"),
        })
    except Exception as e:
        checks.append({"name": "echo_command", "ok": False, "latency_ms": _ms(t0), "error": str(e)})

    # 5. AREA log scrape — confirms MCPLOG is wired up
    t0 = time.perf_counter()
    try:
        area_text = client.read_area_log("MCPLOG", lines=5)
        checks.append({
            "name": "area_log_readable",
            "ok": bool(area_text),
            "latency_ms": _ms(t0),
            "preview": area_text[-200:] if area_text else "",
        })
    except Exception as e:
        checks.append({"name": "area_log_readable", "ok": False, "latency_ms": _ms(t0), "error": str(e)})

    overall = all(c.get("ok", False) for c in checks)
    return {
        "ok": overall,
        "instance": inst.to_dict(),
        "checks": checks,
        "summary": f"{sum(c['ok'] for c in checks)} / {len(checks)} checks passed",
    }
