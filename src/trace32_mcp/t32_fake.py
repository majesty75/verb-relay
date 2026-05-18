"""Fake TRACE32 implementation for tests / CI / dev without an install.

Activated by `T32_MCP_FAKE=1`. Returns deterministic responses so the entire
MCP tool surface can be exercised without TRACE32 (or the t32api library, or
the licensed simulator, or a network) being available.

Design goals:
- Same observable shape as the real client (CommandResult, state() dict, ...).
- Records every command issued, retrievable via `recorder()` for assertions.
- Triggers the error path for any command containing `__FAKE_ERROR__` so tests
  can exercise the structured error reporting.
- Spawn returns a fake T32Instance (pid=0, spawned_by_us=True) so the registry
  + atexit cleanup paths get exercised, but nothing is actually launched.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .t32_client import (
    CommandResult,
    ERROR_MASK,
    MODE_ERROR,
    MODE_INFO,
    T32Endpoint,
    decode_mode,
)

FAKE_ENV = "T32_MCP_FAKE"


def is_fake_mode() -> bool:
    return os.environ.get(FAKE_ENV, "").lower() not in ("", "0", "false", "no")


# --------------------------------------------------------------------------
# Recorder — single-process call log usable from tests
# --------------------------------------------------------------------------

@dataclass
class CallRecord:
    when: float
    endpoint: tuple[str, int, str]
    cmd: str


class _Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[CallRecord] = []

    def add(self, endpoint: T32Endpoint, cmd: str) -> None:
        with self._lock:
            self._calls.append(
                CallRecord(
                    when=time.time(),
                    endpoint=(endpoint.host, endpoint.port, endpoint.node_name),
                    cmd=cmd,
                )
            )

    def all(self) -> list[CallRecord]:
        with self._lock:
            return list(self._calls)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


_RECORDER = _Recorder()


def recorder() -> _Recorder:
    return _RECORDER


# --------------------------------------------------------------------------
# Canned responses — extend per test as needed
# --------------------------------------------------------------------------

_PRINT_RE = re.compile(r'^\s*PRINT\s+(.*)$', re.IGNORECASE)


def _canned_response(cmd: str) -> tuple[str, int]:
    """Map a PRACTICE command to (text, mode_bits). Default: empty text, INFO."""
    if "__FAKE_ERROR__" in cmd:
        return ("fake error injected", MODE_ERROR)

    m = _PRINT_RE.match(cmd)
    if m:
        # Strip surrounding quotes for echo realism
        return (m.group(1).strip().strip('"').strip("'"), MODE_INFO)

    if cmd.startswith("Data.LOAD.Elf"):
        return ("Loaded ELF (fake)", MODE_INFO)
    if cmd.startswith("Data.LOAD.Binary"):
        return ("Loaded binary (fake)", MODE_INFO)
    if cmd.startswith("Break.Set"):
        return ("Breakpoint set (fake)", MODE_INFO)
    if cmd.startswith("Break.List"):
        return ("(fake) 0 breakpoints", MODE_INFO)
    if cmd.startswith("Break.Delete"):
        return ("Breakpoint(s) deleted (fake)", MODE_INFO)
    if cmd.startswith(("Go", "Step")):
        return ("(running)", MODE_INFO)
    if cmd.startswith("Break"):
        return ("(halted)", MODE_INFO)
    if cmd.startswith("Register.view"):
        return ("R0=0x00000000\nR1=0x00000000\nPC=0x08000000  (fake)", MODE_INFO)
    if cmd.startswith("Var.View"):
        return ("(fake) struct = { count = 42, ptr = 0x20000000 }", MODE_INFO)
    if cmd.startswith("sYmbol.LIST"):
        return ("main\ninit\nfoo\nbar  (fake)", MODE_INFO)
    if cmd.startswith("SYStem."):
        return (f"(fake) {cmd}", MODE_INFO)
    if cmd.startswith("AREA."):
        return ("", MODE_INFO)
    if cmd == "QUIT":
        return ("(fake) quitting", MODE_INFO)
    return ("", MODE_INFO)


# --------------------------------------------------------------------------
# FakeT32Client — same surface as T32Client
# --------------------------------------------------------------------------

class FakeT32Client:
    def __init__(self, endpoint: T32Endpoint, keep_open: bool = False) -> None:
        self.endpoint = endpoint
        self._connected = False
        self._area_setup_done = False
        self._lock = threading.Lock()
        self._keep_open = keep_open
        # Simulated AREA buffer
        self._area: dict[str, list[str]] = {}

    # ---- minimal life-cycle parity ------------------------------------------

    def _ensure_connected(self) -> None:
        self._connected = True
        if not self._area_setup_done:
            self._area.setdefault("MCPLOG", [])
            self._area_setup_done = True

    def close(self) -> None:
        self._connected = False
        self._area_setup_done = False

    # ---- core verb ----------------------------------------------------------

    def run(self, line: str) -> CommandResult:
        with self._lock:
            self._ensure_connected()
            _RECORDER.add(self.endpoint, line)
            text, mode = _canned_response(line)
            # Append to MCPLOG
            if text:
                self._area.setdefault("MCPLOG", []).append(text)
            ok = not (mode & ERROR_MASK)
            return CommandResult(
                ok=ok,
                cmd=line,
                text=text,
                mode=mode,
                mode_flags=decode_mode(mode),
                practice_state=2 if not ok else 0,
                error=text if not ok else None,
            )

    def cmd(self, line: str) -> CommandResult:
        return self.run(line)

    def cmd_with_message(self, line: str) -> dict:
        return self.run(line).to_dict()

    def eval_practice(self, expression: str) -> dict:
        return self.run(f"PRINT {expression}").to_dict()

    # ---- state --------------------------------------------------------------

    def state(self) -> dict:
        self._ensure_connected()
        return {
            "raw_state": 2,
            "state": "stopped",
            "cpu": "FakeCortexM4",
            "endpoint": {
                "host": self.endpoint.host,
                "port": self.endpoint.port,
                "node": self.endpoint.node_name,
            },
        }

    # ---- memory -------------------------------------------------------------

    def read_memory(self, address: int, length: int, access: str = "ANY") -> bytes:
        # Pattern: low byte = (address + i) & 0xFF
        return bytes(((address + i) & 0xFF) for i in range(length))

    def write_memory(self, address: int, data: bytes, access: str = "ANY") -> None:
        _RECORDER.add(self.endpoint, f"(fake-write) @0x{address:X} len={len(data)}")

    # ---- AREA log -----------------------------------------------------------

    def read_area_log(self, area: str = "MCPLOG", lines: int | None = None) -> str:
        buf = self._area.get(area, [])
        text = "\n".join(buf)
        if lines is not None:
            text = "\n".join(buf[-lines:])
        return text


# --------------------------------------------------------------------------
# Fake spawn — populates the registry without launching anything
# --------------------------------------------------------------------------

def make_fake_instance(arch: str, port: int | None, node_name: str | None) -> "T32Instance":  # noqa: F821
    from .t32_process import T32Instance, pick_free_port

    chosen_port = port if port is not None else pick_free_port()
    node = node_name or f"FAKE_{arch.upper()}_{chosen_port}"
    return T32Instance(
        node_name=node,
        host="127.0.0.1",
        port=chosen_port,
        arch=arch,
        pid=0,
        binary="(fake)",
        config_path="",
        log_path="",
        work_dir="",
        spawned_by_us=True,  # so shutdown exercises the registry path
    )
