"""Tools that establish or report on T32 connections.

Design: spawn vs attach is an **explicit choice**, never inferred. The model
calls `t32_attach` when given an existing host/port, and `t32_spawn` when
asked to launch a fresh PowerView/Sim. Other tools (load_program, run_practice,
...) only consume already-registered endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..session import all_instances, close_client, get_client
from ..t32_fake import is_fake_mode
from ..t32_process import attach, is_port_open


class AttachInput(BaseModel):
    """Attach to a T32 PowerView/PowerDebug that is already listening on host:port."""

    host: str = Field(description="Hostname or IP of the running T32 instance, e.g. 127.0.0.1 or lab-rack-4.corp.")
    port: int = Field(description="RCL port configured in that T32's config.t32 (PORT= line).")
    node_name: str | None = Field(
        default=None,
        description="Friendly id to refer to this instance later (default: T32_external_<host>_<port>).",
    )
    arch: str = Field(default="unknown", description="Tag with the CPU family if known.")


class DisconnectInput(BaseModel):
    """Drop our cached client. Does NOT shut the T32 process down — use t32_shutdown for that."""

    host: str | None = None
    port: int | None = None
    node_name: str | None = None


class StatusInput(BaseModel):
    """Get state of a specific instance, or — if neither host/port nor node_name given — list all known."""

    host: str | None = None
    port: int | None = None
    node_name: str | None = None


def t32_attach(args: dict) -> dict:
    p = AttachInput(**args)
    if not is_fake_mode() and not is_port_open(p.host, p.port, timeout=1.0):
        return {
            "ok": False,
            "error": f"nothing listening on {p.host}:{p.port}. Either start TRACE32 there first, "
                     f"or use t32_spawn to launch a new one.",
        }
    inst = attach(p.host, p.port, node_name=p.node_name, arch=p.arch)
    # Establish the RCL link now so we surface a real error if config is bad.
    client = get_client(host=inst.host, port=inst.port, node_name=inst.node_name)
    try:
        target = client.state()
    except Exception as e:
        return {"ok": False, "error": str(e), "instance": inst.to_dict()}
    return {"ok": True, "instance": inst.to_dict(), "target": target}


def t32_disconnect(args: dict) -> dict:
    p = DisconnectInput(**args)
    closed = close_client(host=p.host, port=p.port, node_name=p.node_name)
    return {"ok": True, "closed_cached_client": closed, "active_clients_after": all_instances()}


def t32_status(args: dict) -> dict:
    p = StatusInput(**args)
    if p.host is None and p.port is None and p.node_name is None:
        return {"ok": True, "instances": all_instances()}
    client = get_client(host=p.host, port=p.port, node_name=p.node_name)
    try:
        return {"ok": True, "target": client.state()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
