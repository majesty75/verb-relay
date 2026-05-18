"""TRACE32 client via Lauterbach's official PYRCL package.

Replaces the older ctypes-based wrapper. PYRCL is `lauterbach.trace32.rcl` —
a native Python implementation of the RCL protocol (no DLL loading, works
identically on macOS / Linux / Windows). Lauterbach recommends PYRCL for new
projects (app_python.pdf §"PYRCL versus TRACE32 Legacy Approach", p5).

Design:

  * **TCP first, UDP fallback.** PYRCL supports both; the docs say TCP is
    recommended (faster, multi-client, no PACKLEN tuning). We try TCP, and
    if that fails (older T32 build without NETTCP support, network policy,
    ...) we fall back to UDP/NETASSIST. Both RCL sections are written in
    the generated config.t32 (same PORT — UDP and TCP sockets are distinct
    at the OS level).

  * **Same observable surface as before.** Tools still receive a
    CommandResult-shaped dict from every PRACTICE command, including the
    error / mode flags / PRACTICE state. The fake mode (T32_MCP_FAKE=1)
    keeps working untouched.

  * **No more $T32SYS / t32api.py / libt32api*** plumbing.** PYRCL doesn't
    use the shared library — it speaks the RCL wire protocol directly over
    Python sockets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


# Default sequence we try; first one to succeed wins.
DEFAULT_PROTOCOL_PREFERENCE = ("TCP", "UDP")


# ---------------------------------------------------------------------------
# Error-mode parsing (preserved for backward compat with downstream tools).
# PYRCL exposes message mode as an int; we keep our decoded flag names.
# ---------------------------------------------------------------------------

MODE_ERROR_INFO  = 0x01
MODE_ERROR       = 0x02
MODE_STATE       = 0x04
MODE_WARN        = 0x08
MODE_INFO        = 0x10
MODE_TARGET_INFO = 0x20
ERROR_MASK       = MODE_ERROR | MODE_ERROR_INFO

PRACTICE_IDLE = 0
PRACTICE_RUN  = 1
PRACTICE_ERR  = 2


def decode_mode(mode: int) -> list[str]:
    flags = []
    if mode & MODE_ERROR_INFO:  flags.append("ERROR_INFO")
    if mode & MODE_ERROR:       flags.append("ERROR")
    if mode & MODE_STATE:       flags.append("STATE")
    if mode & MODE_WARN:        flags.append("WARN")
    if mode & MODE_INFO:        flags.append("INFO")
    if mode & MODE_TARGET_INFO: flags.append("TARGET_INFO")
    return flags


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

class T32Error(RuntimeError):
    def __init__(self, op: str, code: int = -1, message: str = "") -> None:
        super().__init__(f"T32 {op} failed (code={code}): {message}".strip())
        self.op = op
        self.code = code
        self.message = message


@dataclass(frozen=True)
class T32Endpoint:
    host: str
    port: int
    node_name: str = "T32"
    packet_length: int = 1024
    # `t32sys` is kept for backward compat with attach/spawn signatures,
    # but PYRCL does not use it.
    t32sys: str | None = None


@dataclass
class CommandResult:
    """Outcome of a single PRACTICE command."""
    ok: bool
    cmd: str
    text: str
    mode: int
    mode_flags: list[str]
    practice_state: int
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "cmd": self.cmd, "text": self.text,
            "mode": self.mode, "mode_flags": self.mode_flags,
            "practice_state": self.practice_state, "error": self.error,
        }


# ---------------------------------------------------------------------------
# Connection — TCP-first with UDP fallback
# ---------------------------------------------------------------------------

def _pyrcl():
    """Lazy import so the package is only required at first connect."""
    import lauterbach.trace32.rcl as t32  # type: ignore
    return t32


def try_connect(
    host: str, port: int,
    *, protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    packlen: int = 1024, timeout: float = 10.0,
) -> tuple[Any, str]:
    """Try each protocol in order. Returns (dbg, protocol_used).

    Raises T32Error if every protocol fails.
    """
    t32 = _pyrcl()
    last_error: Exception | None = None
    for proto in protocols:
        try:
            if proto.upper() == "TCP":
                dbg = t32.connect(node=host, port=port, protocol="TCP", timeout=timeout)
            elif proto.upper() == "UDP":
                dbg = t32.connect(
                    node=host, port=port, protocol="UDP",
                    packlen=packlen, timeout=timeout,
                )
            else:
                raise ValueError(f"unsupported protocol {proto!r}")
            return dbg, proto.upper()
        except Exception as e:  # noqa: BLE001 — PYRCL raises various types
            last_error = e
            continue
    raise T32Error(
        "PYRCL connect", message=(
            f"could not reach RCL at {host}:{port} over any of {list(protocols)}. "
            f"Last error: {last_error!r}"
        ),
    )


def is_rcl_responsive(
    host: str, port: int,
    t32sys: str | None = None,  # accepted for back-compat; unused by PYRCL
    *, protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    timeout_per_try: float = 2.0,
) -> bool:
    """Quick readiness probe — try to PYRCL-connect, then close."""
    try:
        dbg, _ = try_connect(host, port, protocols=protocols, timeout=timeout_per_try)
    except Exception:
        return False
    try:
        dbg.disconnect()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# T32Client — drop-in for the old ctypes wrapper
# ---------------------------------------------------------------------------

class T32Client:
    """One client per logical T32 instance. Thread-safe."""

    def __init__(
        self,
        endpoint: T32Endpoint,
        *,
        keep_open: bool = True,
        protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    ) -> None:
        self.endpoint = endpoint
        self._lock = threading.Lock()
        self._keep_open = keep_open
        self._dbg = None
        self._connected_protocol: str | None = None
        self._area_setup_done = False
        self._protocols = protocols

    # ---- lifecycle ----------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._dbg is not None:
            return
        dbg, proto = try_connect(
            self.endpoint.host, self.endpoint.port,
            protocols=self._protocols,
            packlen=self.endpoint.packet_length,
            timeout=10.0,
        )
        self._dbg = dbg
        self._connected_protocol = proto
        if not self._area_setup_done:
            self._setup_area()
            self._area_setup_done = True

    def _setup_area(self) -> None:
        """Create our dedicated MCPLOG AREA so command output is always
        retrievable even if the user has no AREA window open."""
        for setup_cmd in (
            "AREA.CREATE MCPLOG 200. 1000.",
            "AREA.Select MCPLOG",
            "AREA.CLEAR MCPLOG",
        ):
            try:
                self._dbg.cmd(setup_cmd)
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            if self._dbg is not None:
                try:
                    self._dbg.disconnect()
                except Exception:
                    pass
                self._dbg = None
                self._connected_protocol = None
                self._area_setup_done = False

    @property
    def connected_protocol(self) -> str | None:
        return self._connected_protocol

    # ---- core verb ----------------------------------------------------------

    def run(self, line: str, *, capture_area: bool = True) -> CommandResult:
        """Run one PRACTICE command line and return a typed result.

        Output capture:
          * `get_message()` returns the LAST POPUP, not the AREA log. Many
            commands don't pop a message, so it returns stale text from a
            previous popup. We treat that as advisory only.
          * When `capture_area=True` we clear MCPLOG before, run the command,
            then read MCPLOG after — that's what gives the AI the actual
            PRINT/echo/error output of the command. AREA capture costs an
            extra round-trip so callers can disable it for tight loops.
        """
        with self._lock:
            try:
                self._ensure_connected()
            except Exception as e:
                return CommandResult(
                    ok=False, cmd=line, text="", mode=0, mode_flags=[],
                    practice_state=0, error=f"connect failed: {e}",
                )
            # Clear AREA so output of THIS command isn't mixed with previous.
            if capture_area:
                try:
                    self._dbg.cmd("AREA.CLEAR MCPLOG")
                except Exception:
                    pass
            popup_text = ""
            area_text = ""
            mode = 0
            pstate = 0
            err: str | None = None
            try:
                self._dbg.cmd(line)
            except Exception as e:
                err = f"cmd raised: {e}"
            # Pull popup + practice state (always cheap).
            try:
                msg = self._dbg.get_message()
                if isinstance(msg, tuple) and len(msg) >= 2:
                    popup_text, mode = str(msg[0]), int(msg[1])
                else:
                    popup_text = "" if msg is None else str(msg)
            except Exception:
                pass
            try:
                # PRACTICE.STATE() returns 0=idle, 1=run, 2=error per
                # general_func.pdf. PYRCL has no direct accessor — go via fnc.
                pstate = int(self._dbg.fnc("PRACTICE.STATE()"))
            except Exception:
                pstate = 0  # unknown — don't false-fail because we couldn't ask
            # Capture AREA output of this specific command.
            if capture_area and err is None:
                try:
                    area_text = self._read_area_inline("MCPLOG")
                except Exception:
                    pass
            ok = (err is None) and (not (mode & ERROR_MASK)) and (pstate != PRACTICE_ERR)
            text = area_text or popup_text  # prefer AREA (cmd-scoped) over popup (global)
            if not ok and err is None:
                err = text.strip() or (
                    f"mode={decode_mode(mode)} practice_state={pstate}"
                )
            return CommandResult(
                ok=ok, cmd=line, text=text,
                mode=mode, mode_flags=decode_mode(mode),
                practice_state=pstate, error=err,
            )

    def _read_area_inline(self, area: str = "MCPLOG") -> str:
        """Read the named AREA via AREA.SAVE → tempfile (caller holds the lock).

        Only useful when MCP and TRACE32 share a filesystem. Returns "" if
        no shared FS (remote PowerDebug).
        """
        import tempfile, time as _time
        from pathlib import Path as _P
        # Per-endpoint filename so concurrent reads across instances don't
        # clobber each other on the host filesystem (the T32-side AREA is
        # already per-instance, but the dump file we read back is on us).
        tmp = _P(tempfile.gettempdir()) / f"trace32_mcp_area_{self.endpoint.port}_{area}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        try:
            self._dbg.cmd(f'AREA.SAVE "{tmp.as_posix()}" {area}')
        except Exception:
            return ""
        for _ in range(20):
            if tmp.exists():
                break
            _time.sleep(0.025)
        if not tmp.exists():
            return ""
        try:
            txt = tmp.read_text(errors="replace")
        finally:
            try: tmp.unlink()
            except OSError: pass
        return txt

    # Back-compat aliases
    def cmd(self, line: str) -> CommandResult:
        return self.run(line)

    def cmd_with_message(self, line: str) -> dict:
        return self.run(line).to_dict()

    def eval_practice(self, expression: str) -> dict:
        """Evaluate a PRACTICE expression via PYRCL's fnc() — returns the
        real value, not a stringified popup. Falls back to PRINT+AREA capture
        if fnc raises (some expressions aren't valid `fnc()` inputs).
        """
        with self._lock:
            self._ensure_connected()
            try:
                value = self._dbg.fnc(expression)
                return {
                    "ok": True, "expression": expression,
                    "value": value if isinstance(value, (int, float, str, bool, type(None)))
                             else repr(value),
                    "value_type": type(value).__name__,
                    "method": "fnc",
                }
            except Exception as e:
                # Fall through to PRINT path
                fnc_err = str(e)
        res = self.run(f"PRINT {expression}").to_dict()
        res["fnc_error"] = fnc_err
        res["method"] = "print"
        return res

    # ---- state --------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Report system state (down/halted/running) + CPU + endpoint."""
        with self._lock:
            self._ensure_connected()
            state_int = 0
            cpu = ""
            try:
                state_int = int(self._dbg.get_state())
            except Exception:
                pass
            state_map = {0: "down", 1: "halted_no_debugger", 2: "stopped", 3: "running"}
            try:
                cpu = str(self._dbg.fnc("STRing.UPpeR(CPU())"))
            except Exception:
                pass
            return {
                "raw_state": state_int,
                "state": state_map.get(state_int, "unknown"),
                "cpu": cpu,
                "protocol": self._connected_protocol,
                "endpoint": {
                    "host": self.endpoint.host,
                    "port": self.endpoint.port,
                    "node": self.endpoint.node_name,
                },
            }

    # ---- memory -------------------------------------------------------------

    def _addr(self, address: int, access: str = "ANY"):
        """Build a PYRCL Address from (int, access-class-string).

        PYRCL's address.from_string parses things like "D:0x100", "P:0x100".
        ANY → no access prefix.
        """
        access = (access or "ANY").upper()
        prefix = "" if access in ("", "ANY") else f"{access}:"
        return self._dbg.address.from_string(f"{prefix}0x{address:X}")

    def read_memory(self, address: int, length: int, access: str = "ANY") -> bytes:
        with self._lock:
            self._ensure_connected()
            addr = self._addr(address, access)
            return bytes(self._dbg.memory.read(addr, length=length))

    def write_memory(self, address: int, data: bytes, access: str = "ANY") -> None:
        with self._lock:
            self._ensure_connected()
            addr = self._addr(address, access)
            self._dbg.memory.write(addr, bytes(data))

    # ---- registers ----------------------------------------------------------

    def read_registers(self) -> list[dict]:
        """Read all CPU registers via PYRCL's native register.read_all().

        Returns a list of {name, value, unit, core} dicts.
        """
        with self._lock:
            self._ensure_connected()
            regs = self._dbg.register.read_all()
            out = []
            for r in regs:
                out.append({
                    "name":  getattr(r, "name", None),
                    "value": getattr(r, "value", None),
                    "unit":  getattr(r, "unit", None),
                    "core":  getattr(r, "core", None),
                })
            return out

    def read_register(self, name: str) -> dict:
        with self._lock:
            self._ensure_connected()
            r = self._dbg.register.read(name)
            return {
                "name":  getattr(r, "name", name),
                "value": getattr(r, "value", None),
                "unit":  getattr(r, "unit", None),
                "core":  getattr(r, "core", None),
            }

    def write_register(self, name: str, value: int) -> None:
        with self._lock:
            self._ensure_connected()
            self._dbg.register.write_by_name(name, int(value))

    # ---- symbols ------------------------------------------------------------

    def symbol_query(self, name: str) -> dict | None:
        """Look up a single symbol by name. Returns None if not found."""
        with self._lock:
            self._ensure_connected()
            try:
                s = self._dbg.symbol.query_by_name(name)
            except Exception:
                return None
            if s is None:
                return None
            return {
                "name":    getattr(s, "name", name),
                "address": getattr(getattr(s, "address", None), "value", None),
                "size":    getattr(s, "size", None),
                "type":    getattr(s, "type", None) and str(s.type),
            }

    def symbol_list(self, pattern: str = "*", limit: int = 200) -> list[dict]:
        """Glob-style symbol listing.

        PYRCL's `symbol` service only exposes `query_by_name`, no glob walker.
        Fall back to a PRACTICE pipeline that writes one symbol per line to a
        temp file via sYmbol.LIST (output goes to AREA → we save+parse).
        """
        with self._lock:
            self._ensure_connected()
            try:
                self._dbg.cmd("AREA.CLEAR MCPLOG")
                # sYmbol.List is a window; for inline output use sYmbol.List.*
                # Use Function listing which AREAs the names.
                self._dbg.cmd(f"sYmbol.List.Function {pattern}")
            except Exception as e:
                return [{"_error": f"sYmbol.List.Function failed: {e}"}]
            txt = self._read_area_inline("MCPLOG")
        # Best-effort line parse: each non-empty token-line is a candidate name.
        out = []
        for line in txt.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            tokens = line.split()
            if tokens:
                out.append({"raw": line, "name": tokens[0]})
            if len(out) >= limit:
                break
        return out

    # ---- breakpoints --------------------------------------------------------

    def bp_list(self) -> list[dict]:
        with self._lock:
            self._ensure_connected()
            try:
                bps = self._dbg.breakpoint.list()
            except Exception as e:
                return [{"_error": str(e)}]
            out = []
            for b in bps:
                out.append({
                    "address": getattr(getattr(b, "address", None), "value", None),
                    "type":    str(getattr(b, "type_", getattr(b, "type", None))),
                    "impl":    str(getattr(b, "impl", None)),
                    "enabled": getattr(b, "enabled", None),
                    "core":    getattr(b, "core", None),
                })
            return out

    # ---- execution (native PYRCL — preferred over `Go`/`Break` PRACTICE) ----

    def native_go(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.go()

    def native_break(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.break_()

    def native_step(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.step()

    def native_step_over(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.step_over()

    def native_step_asm(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.step_asm()

    def native_go_return(self):
        with self._lock:
            self._ensure_connected()
            self._dbg.go_return()

    # ---- AREA log -----------------------------------------------------------

    def read_area_log(self, area: str = "MCPLOG", lines: int | None = None) -> str:
        """Save the named AREA to a temp file on the T32 host and read it back.

        Only useful when the MCP and T32 share a filesystem (true for local
        sim). For remote PowerDebug returns the path-style empty result and
        the caller should fall back to per-command CommandResult.text.
        """
        import tempfile
        import time as _time
        from pathlib import Path as _P

        # Per-endpoint filename so concurrent reads across instances don't
        # clobber each other on the host filesystem (the T32-side AREA is
        # already per-instance, but the dump file we read back is on us).
        tmp = _P(tempfile.gettempdir()) / f"trace32_mcp_area_{self.endpoint.port}_{area}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

        with self._lock:
            self._ensure_connected()
            try:
                self._dbg.cmd(f'AREA.SAVE "{tmp}" {area}')
            except Exception:
                return ""

        for _ in range(20):
            if tmp.exists():
                break
            _time.sleep(0.05)
        if not tmp.exists():
            return ""
        text = tmp.read_text(errors="replace")
        try:
            tmp.unlink()
        except OSError:
            pass
        if lines is not None:
            text = "\n".join(text.splitlines()[-lines:])
        return text
