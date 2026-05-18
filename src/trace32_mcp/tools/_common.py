"""Shared helpers for tool handlers.

Tools never silently spawn or attach — they resolve a *registered* target and
fail clearly if the AI forgot to call t32_spawn / t32_attach first. This keeps
spawn/attach an explicit decision (see tools/session.py + tools/instances.py).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..t32_client import T32Client
from ..t32_process import T32Instance, registry
from ..session import get_client


class TargetSelector(BaseModel):
    """How a tool picks which T32 instance to act on.

    Resolution order (all optional):
      1. `node_name` — look up that registered instance.
      2. `host` + `port` — match a registered endpoint.
      3. Neither: use the most recently registered instance.
    """

    node_name: str | None = Field(
        default=None,
        description="Instance node_name returned from t32_spawn / t32_attach / t32_list_instances. "
                    "Preferred when juggling multiple instances.",
    )
    host: str | None = Field(default=None, description="Endpoint host. Use with `port` instead of node_name.")
    port: int | None = Field(default=None, description="Endpoint port.")


def resolve_target(p: TargetSelector | dict) -> tuple[T32Instance, T32Client]:
    """Find the requested instance + a T32Client bound to it.

    Raises LookupError with an AI-actionable message if nothing matches.
    """
    if isinstance(p, dict):
        p = TargetSelector(**p)
    reg = registry()
    inst: T32Instance | None = None

    if p.node_name:
        inst = reg.get_by_node(p.node_name)
        if inst is None:
            raise LookupError(
                f"no T32 instance registered with node_name={p.node_name!r}. "
                f"Available: {[i.node_name for i in reg.list()] or '(none — call t32_spawn or t32_attach first)'}"
            )
    elif p.host is not None and p.port is not None:
        inst = reg.get_by_endpoint(p.host, p.port)
        if inst is None:
            raise LookupError(
                f"no T32 instance registered for endpoint {p.host}:{p.port}. "
                f"If a T32 is running there, call t32_attach first. To launch a new one, call t32_spawn."
            )
    else:
        lst = reg.list()
        if not lst:
            raise LookupError(
                "no T32 instances registered yet. Call t32_attach (existing T32) or t32_spawn (launch new) first."
            )
        inst = lst[-1]  # most recent

    client = get_client(host=inst.host, port=inst.port, node_name=inst.node_name)
    return inst, client


def hexdump(data: bytes, address: int, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{address + i:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)
