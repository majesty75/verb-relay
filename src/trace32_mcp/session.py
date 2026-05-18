"""Client + instance lookup used by tool handlers.

Decisions:
- Connection state lives in `t32_process.InstanceRegistry`.
- `T32Client` objects are short-lived (cached per endpoint here).
- `get_or_create_client(host, port, ...)` does "spawn if nothing listens,
  attach otherwise" (the user-chosen model).
"""

from __future__ import annotations

import threading
from typing import Any

from .config import load_config
from .t32_client import T32Client, T32Endpoint
from .t32_fake import FakeT32Client, is_fake_mode
from .t32_process import (
    T32Instance,
    attach,
    connect_or_spawn,
    is_port_open,
    registry,
)


_CLIENT_LOCK = threading.Lock()
_CLIENTS: dict[tuple[str, int, str], T32Client] = {}


def _endpoint(
    host: str | None,
    port: int | None,
    node_name: str | None,
    t32sys: str | None,
) -> T32Endpoint:
    cfg = load_config()
    return T32Endpoint(
        host=host or cfg.host,
        port=port if port is not None else cfg.port,
        node_name=node_name or cfg.node_name,
        packet_length=cfg.packet_length,
        t32sys=t32sys or cfg.t32sys,
    )


def get_client(
    host: str | None = None,
    port: int | None = None,
    node_name: str | None = None,
    t32sys: str | None = None,
) -> T32Client:
    """Get a cached T32Client for the given endpoint.

    Does NOT spawn. Assumes the endpoint already exists; callers that want
    spawn-if-missing should use `ensure_instance()`.
    """
    ep = _endpoint(host, port, node_name, t32sys)
    key = (ep.host, ep.port, ep.node_name)
    with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = FakeT32Client(ep) if is_fake_mode() else T32Client(ep)
            _CLIENTS[key] = client
        return client


def ensure_instance(
    *,
    host: str | None = None,
    port: int | None = None,
    node_name: str | None = None,
    arch: str = "arm",
    t32sys: str | None = None,
    auto_spawn: bool = True,
    headless: bool = False,
) -> tuple[T32Instance, T32Client]:
    """Spawn-or-attach an instance and return both the registry record and a client.

    If `auto_spawn=False`, only attach to an already-running T32 (raises if
    nothing is listening).
    """
    cfg = load_config()
    target_host = host or cfg.host
    target_port = port  # None means "any free port" (only honored when spawning)

    if target_port is not None and is_port_open(target_host, target_port):
        inst = attach(target_host, target_port, node_name=node_name, arch=arch)
    elif auto_spawn:
        inst = connect_or_spawn(
            arch=arch,
            host=target_host,
            port=target_port,
            node_name=node_name,
            t32sys=t32sys or cfg.t32sys,
            headless=headless,
        )
    else:
        raise ConnectionError(
            f"nothing listening on {target_host}:{target_port} and auto_spawn=False"
        )

    client = get_client(host=inst.host, port=inst.port, node_name=inst.node_name, t32sys=t32sys)
    return inst, client


def close_client(
    host: str | None = None,
    port: int | None = None,
    node_name: str | None = None,
) -> bool:
    ep = _endpoint(host, port, node_name, None)
    key = (ep.host, ep.port, ep.node_name)
    with _CLIENT_LOCK:
        client = _CLIENTS.pop(key, None)
    if client is None:
        return False
    client.close()
    return True


def list_clients() -> list[dict[str, Any]]:
    with _CLIENT_LOCK:
        return [
            {"host": k[0], "port": k[1], "node_name": k[2], "connected": v._connected}
            for k, v in _CLIENTS.items()
        ]


def shutdown_instance(node_name: str, force: bool = False) -> dict:
    """Shut down a tracked instance (RCL Quit + SIGTERM if we spawned it)."""
    # Also drop any cached client for the same endpoint
    inst = registry().get_by_node(node_name)
    if inst is not None:
        close_client(host=inst.host, port=inst.port, node_name=inst.node_name)
    return registry().shutdown(node_name, force=force)


def all_instances() -> list[dict]:
    return [i.to_dict() for i in registry().list()]
