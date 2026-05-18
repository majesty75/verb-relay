"""t32_healthcheck — verify a registered T32 instance is actually responsive.

Different from t32_status (which reports debugger state). Healthcheck answers:
  "If I send a command right now, will TRACE32 receive and execute it?"
"""

from __future__ import annotations

import time

from pydantic import Field

from ..session import get_client
from ..t32_process import is_port_open, is_rcl_responsive, registry
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

    # 1. RCL handshake (UDP) — the authoritative readiness signal for
    # RCL=NETASSIST endpoints. A pure TCP probe would always fail here.
    t0 = time.perf_counter()
    rcl_ok = is_rcl_responsive(inst.host, inst.port, t32sys=getattr(inst, "t32sys", None),
                               timeout_per_try=1.5)
    checks.append({
        "name": "rcl_handshake",
        "ok": rcl_ok,
        "latency_ms": _ms(t0),
        "detail": f"T32_Init+T32_Attach round-trip to {inst.host}:{inst.port}",
    })

    # 2. Side-channel TCP probe — informational only. A T32 running with
    # `RCL=NETASSIST` will NOT have this port open over TCP, so a False here
    # is expected. A True can hint there's something else (e.g. NETTCP)
    # bound to the port.
    t0 = time.perf_counter()
    tcp_ok = is_port_open(inst.host, inst.port, timeout=0.5)
    checks.append({
        "name": "tcp_port_open",
        "ok": tcp_ok,
        "informational": True,  # excluded from `overall` verdict
        "latency_ms": _ms(t0),
        "detail": "informational — RCL=NETASSIST is UDP so False is expected",
    })

    if not rcl_ok:
        return {"ok": False, "instance": inst.to_dict(), "checks": checks,
                "error": f"RCL not responding at {inst.host}:{inst.port}"}

    # 3. Client setup
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

    # 4. Trivial command echo (PRINT). "ok" means PRACTICE ran without an
    # error flag — we do NOT require the echo string to appear in `text`,
    # because TRACE32 may return TARGET_INFO popups (e.g. "Floating license
    # gets checked on first Go or Step") even when PRACTICE succeeded.
    t0 = time.perf_counter()
    try:
        res = client.run(p.probe).to_dict()
        echo_ok = res.get("ok", False)
        echo_found = "trace32-mcp" in (res.get("text") or "")
        checks.append({
            "name": "echo_command",
            "ok": echo_ok,
            "latency_ms": _ms(t0),
            "cmd": p.probe,
            "text": (res.get("text") or "")[:200],
            "echo_string_found": echo_found,
            "mode_flags": res.get("mode_flags", []),
            "practice_state": res.get("practice_state"),
        })
    except Exception as e:
        checks.append({"name": "echo_command", "ok": False, "latency_ms": _ms(t0), "error": str(e)})

    # 5. AREA log scrape — confirms MCPLOG is wired up. "Empty" is OK as long
    # as the AREA.SAVE round-trip succeeded; we record the size as evidence.
    t0 = time.perf_counter()
    try:
        area_text = client.read_area_log("MCPLOG", lines=5)
        checks.append({
            "name": "area_log_readable",
            # Round-trip worked → True even if empty (a freshly cleared AREA
            # is the expected state right after MCPLOG setup).
            "ok": True,
            "latency_ms": _ms(t0),
            "preview": area_text[-200:] if area_text else "",
            "empty": not bool(area_text),
        })
    except Exception as e:
        checks.append({"name": "area_log_readable", "ok": False, "latency_ms": _ms(t0), "error": str(e)})

    # Informational checks (e.g. TCP probe against a UDP RCL endpoint) don't
    # count against the overall verdict.
    authoritative = [c for c in checks if not c.get("informational")]
    overall = all(c.get("ok", False) for c in authoritative)
    return {
        "ok": overall,
        "instance": inst.to_dict(),
        "checks": checks,
        "summary": f"{sum(c['ok'] for c in authoritative)} / {len(authoritative)} authoritative checks passed",
    }
