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
                # PRACTICE script run-state via the native RCL call
                # (T32_GetPracticeState): 0 = idle, 1 = a PRACTICE script is
                # still running. There is NO `PRACTICE.STATE()` PRACTICE
                # function — calling one would raise "no function ... exists,
                # don't use commands as functions" and pop an error on every
                # command. Per-command error detection is handled by the
                # message ERROR_MASK below, not by this state.
                pstate = int(self._dbg._get_practice_state())
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
            # AREA.SAVE saves the SELECTED area and takes NO area-name argument
            # (passing one raises "no more arguments expected"). Select first.
            self._dbg.cmd(f"AREA.Select {area}")
            self._dbg.cmd(f'AREA.SAVE "{tmp.as_posix()}"')
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

    def _enumerate_symbol_names(self, pattern: str, limit: int = 1000) -> tuple[list[str], str | None]:
        """Return LEAF symbol names matching a TRACE32 wildcard via sYmbol.ForEach.

        `sYmbol.ForEach "<cmd>" <wildcard>` iterates every symbol matching the
        wildcard and substitutes the FULLY-QUALIFIED name (e.g. `\\sieve\\func\\v`)
        for `*` in <cmd>. We PRINT each with a marker and parse it back. Two
        things learned the hard way on a real target:

          * We capture from the DEFAULT message area A000 (where PRINT lands)
            instead of juggling a private MCPLOG area — fewer commands, fewer
            moving parts (the marker prefix lets us ignore A000's other noise).
          * ForEach yields `\\`-qualified names. A leading backslash makes `fnc()`
            treat the string as a PRACTICE macro, so EVERY downstream
            Var.*/sYmbol.TYPE re-query failed and results came back empty. We
            therefore return the LEAF (last `\\`-separated component), which
            resolves cleanly in Var.VALUE/Var.STRing/sYmbol.TYPE.

        Returns (leaf_names, error). Caller must NOT hold the lock.
        """
        import tempfile, time as _time
        from pathlib import Path as _P
        marker = "T32SYM:"
        tmp = _P(tempfile.gettempdir()) / f"trace32_mcp_enum_{self.endpoint.port}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

        with self._lock:
            self._ensure_connected()
            try:
                # Select the default area A000 so the ForEach PRINTs land there,
                # then capture by saving the SELECTED area to a file. AREA.SAVE
                # takes NO area-name argument — it saves whatever AREA.Select
                # chose (passing a name raises "no more arguments expected").
                self._dbg.cmd("AREA.Select A000")
                self._dbg.cmd("AREA.CLEAR A000")
                # Doubled "" → a literal " in the PRACTICE string; * is replaced
                # by ForEach with the matched symbol's (qualified) name.
                self._dbg.cmd(f'sYmbol.ForEach "PRINT ""{marker}*""" {pattern}')
                # ForEach runs as a PRACTICE script — poll fast, bail when idle.
                for _ in range(150):
                    try:
                        if int(self._dbg._get_practice_state()) == 0:
                            break
                    except Exception:
                        break
                    _time.sleep(0.02)
                self._dbg.cmd(f'AREA.SAVE "{tmp.as_posix()}"')
            except Exception as e:
                return [], f"sYmbol.ForEach failed: {e}"

        for _ in range(20):
            if tmp.exists():
                break
            _time.sleep(0.02)
        try:
            txt = tmp.read_text(errors="replace")
        except Exception:
            return [], "could not read enumeration capture file (shared FS?)"
        finally:
            try: tmp.unlink()
            except OSError: pass

        names: list[str] = []
        seen: set[str] = set()
        for line in txt.splitlines():
            idx = line.find(marker)
            if idx == -1:
                continue
            # Everything after the marker is the symbol name (no spaces in T32
            # symbol names). Globals come back bare ('varray'); locals come back
            # `\\`-qualified ('\\sieve\\func10\\v1') — take the leaf either way.
            raw = line[idx + len(marker):].strip().split()[0] if line[idx + len(marker):].strip() else ""
            leaf = raw.replace("/", "\\").rsplit("\\", 1)[-1].strip()
            if leaf and leaf not in seen:
                seen.add(leaf)
                names.append(leaf)
            if len(names) >= limit:
                break
        return names, None

    def _names_for_pattern(self, pattern: str, limit: int = 1000) -> tuple[list[str], str | None]:
        """Names matching `pattern` — fast path for exact (non-wildcard) names.

        A bare name (no * ? [) is taken verbatim: NO sYmbol.ForEach is issued, so
        the common "read variable X" case costs zero PRACTICE commands (just
        fnc() reads downstream) — important under the unlicensed-sim 50-command
        cap — and returns instantly. Wildcards go through ForEach enumeration.
        """
        if not any(c in pattern for c in "*?["):
            return [pattern], None
        return self._enumerate_symbol_names(pattern, limit=limit)

    def _symbol_type(self, name: str) -> int | None:
        """Numeric symbol class via sYmbol.TYPE() (caller holds the lock).

        0=not found, 1=plain label, 2=HLL function, 3=HLL variable.
        Returns None if the function couldn't be evaluated.
        """
        try:
            return int(self._dbg.fnc(f"sYmbol.TYPE({name})"))
        except Exception:
            return None

    def _read_one_global(self, name: str, max_value_len: int = 4000) -> dict | None:
        """Read one variable's type/address/size/value via Var.* functions.

        Caller MUST hold self._lock. Returns None when `name` is not a readable
        variable (so functions/labels matched by the wildcard get dropped).
        Uses only well-established PRACTICE functions:
          Var.TYPEOF / Var.SIZEOF / Var.ADDRESS / Var.VALUE / Var.STRing
        Var.STRing gives the full formatted value for scalars AND aggregates
        (structs/arrays/unions) in a single round-trip; we truncate it so a huge
        struct can't blow up the response.
        """
        def _f(expr: str):
            try:
                return self._dbg.fnc(expr)
            except Exception:
                return None

        # Keep only HLL variables (sYmbol.TYPE == 3); drops functions/labels.
        if self._symbol_type(name) != 3:
            return None

        type_v = _f(f"Var.TYPEOF({name})")
        value_v = _f(f"Var.STRing({name})")
        entry: dict = {"name": name, "type": "" if type_v is None else str(type_v)}

        addr_v = _f(f"Var.ADDRESS({name})")
        if addr_v is not None:
            try:
                entry["address"] = hex(int(addr_v))
            except Exception:
                entry["address"] = str(addr_v)

        size_v = _f(f"Var.SIZEOF({name})")
        if size_v is not None:
            try:
                entry["size"] = int(size_v)
            except Exception:
                entry["size"] = size_v

        # Numeric/typed value — only meaningful for scalars/enums/pointers.
        # For arrays/structs/unions Var.VALUE() returns the base ADDRESS, which
        # is misleading as a "value", so skip it for aggregates (use the
        # formatted `value`, or t32_inspect_structure, for those).
        type_str = entry["type"]
        is_aggregate = ("[" in type_str) or ("struct" in type_str) or ("union" in type_str)
        if not is_aggregate:
            scalar_v = _f(f"Var.VALUE({name})")
            if isinstance(scalar_v, (int, float, bool)):
                entry["scalar_value"] = scalar_v

        if value_v is not None:
            s = str(value_v)
            if len(s) > max_value_len:
                entry["value"] = s[:max_value_len]
                entry["value_truncated"] = True
            else:
                entry["value"] = s
        # Arrays whose Var.STRing came back empty: point the caller at the tool
        # that can actually expand them.
        if is_aggregate and not entry.get("value"):
            entry["note"] = "aggregate — use t32_inspect_structure for members/values"
        return entry

    def search_variables(self, pattern: str, limit: int = 200) -> list[dict]:
        """Search global variables matching a wildcard — names + type/addr/size.

        Enumerates via sYmbol.ForEach, then keeps only symbols that resolve as
        readable variables (Var.TYPEOF succeeds). Functions/labels are dropped.
        For values, use read_globals (or t32_inspect_structure for a tree).
        """
        names, err = self._names_for_pattern(pattern, limit=limit * 5)
        if err:
            return [{"_error": err}]

        out: list[dict] = []
        with self._lock:
            self._ensure_connected()
            for name in names:
                def _f(expr: str):
                    try:
                        return self._dbg.fnc(expr)
                    except Exception:
                        return None
                if self._symbol_type(name) != 3:
                    continue  # keep only HLL variables
                entry: dict = {"name": name, "type": str(_f(f"Var.TYPEOF({name})") or "")}
                addr_v = _f(f"Var.ADDRESS({name})")
                if addr_v is not None:
                    try:
                        entry["address"] = hex(int(addr_v))
                    except Exception:
                        entry["address"] = str(addr_v)
                size_v = _f(f"Var.SIZEOF({name})")
                if size_v is not None:
                    try:
                        entry["size"] = int(size_v)
                    except Exception:
                        entry["size"] = size_v
                out.append(entry)
                if len(out) >= limit:
                    break
        return out

    def _resolve_variable_name(self, name: str) -> tuple[str | None, list[str]]:
        """Resolve a possibly-partial variable name to an exact one.

        This is what lets a caller inspect a variable WITHOUT knowing its full
        name. Returns (resolved_name | None, candidates):
          * exact name that resolves         → (name, [name])
          * wildcard/substring → 1 variable  → (that_name, [that_name])
          * wildcard/substring → many vars   → (None, [all matches])  (disambiguate)
          * nothing matches                  → (None, [])
        """
        has_wild = any(c in name for c in "*?[")

        # 1. Exact name first (cheap) — accept if it resolves to a variable.
        if not has_wild:
            with self._lock:
                self._ensure_connected()
                if self._symbol_type(name) == 3:
                    return name, [name]

        # 2. Treat the input as a pattern (wrap a bare substring in *...*).
        pattern = name if has_wild else f"*{name}*"
        names, err = self._enumerate_symbol_names(pattern, limit=200)
        if err or not names:
            return None, []

        # Keep only HLL variables (sYmbol.TYPE == 3).
        variables: list[str] = []
        with self._lock:
            self._ensure_connected()
            for nm in names:
                if self._symbol_type(nm) == 3:
                    variables.append(nm)
        if len(variables) == 1:
            return variables[0], variables
        return None, variables

    def read_globals(self, pattern: str, *, max_vars: int = 100,
                     max_value_len: int = 4000) -> dict:
        """One-shot: match globals by wildcard and return their VALUES.

        This is the tool-facing primitive behind t32_read_globals — give it a
        glob like 'g_*' or '*state*' and it returns each matching variable's
        name, type, address, size, and formatted value. Aggregate values
        (structs/arrays) come back as a single formatted string, truncated to
        `max_value_len` so a large structure cannot overflow the response.

        NOTE: values flow through TRACE32's eval result (Var.STRing), which the
        RCL link caps at a few KB — so a large struct/array value is necessarily
        partial here. t32_inspect_structure uses an unlimited file-based dump and
        is the right tool when you need the whole thing.
        """
        names, err = self._names_for_pattern(pattern, limit=max_vars * 5)
        if err:
            return {"ok": False, "pattern": pattern, "error": err}

        out: list[dict] = []
        with self._lock:
            self._ensure_connected()
            for name in names:
                entry = self._read_one_global(name, max_value_len=max_value_len)
                if entry is not None:
                    out.append(entry)
                if len(out) >= max_vars:
                    break

        result = {"ok": True, "pattern": pattern, "count": len(out), "variables": out}
        if any(v.get("value_truncated") for v in out):
            result["note"] = (
                "Some values were truncated to max_value_len. For a bounded, "
                "structured member tree of a large struct, call t32_inspect_structure "
                "with max_depth/max_members."
            )
        return result

    def inspect_structure(self, name: str, *, max_depth: int = 4,
                          max_members: int = 50, max_bytes: int = 2_000_000) -> dict:
        """Inspect a structure recursively → a JSON member tree, with bounds.

        Dumps `Var.View` of the variable to a temp file via WinPrint, parses it
        into a nested tree, then prunes to `max_depth` levels and `max_members`
        children per node so a very large structure cannot overflow the
        response. `max_bytes` caps how much of the dump file we read at all.
        Truncations are flagged in-tree (`truncated`, `members_omitted`).
        """
        import tempfile
        import time as _time
        from pathlib import Path as _P

        # Resolve a partial/wildcard name to an exact one. This is how the
        # caller can drill into a struct without knowing its full name.
        resolved, candidates = self._resolve_variable_name(name)
        if resolved is None:
            if candidates:
                return {
                    "ok": False,
                    "need_disambiguation": True,
                    "query": name,
                    "candidates": candidates[:50],
                    "hint": "Several variables match. Re-call with the exact 'name' from candidates.",
                }
            return {
                "ok": False,
                "query": name,
                "error": f"No variable matched '{name}'. "
                         "Call t32_search_variables to browse names, or use a wildcard like '*name*'.",
            }
        name = resolved

        # Create a temp file path that both Python and TRACE32 can access
        tmp = _P(tempfile.gettempdir()) / f"trace32_mcp_struct_{self.endpoint.port}_{int(_time.time())}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

        with self._lock:
            self._ensure_connected()
            try:
                # Set printer file to temp file in ASCII format
                self._dbg.cmd(f'PRinTer.FILE "{tmp.as_posix()}" ASCII')
                # Redirect a fully-expanded, typed Var.View to the printer file.
                # %TYPE  → prefix each element with its C type (parser relies on this)
                # %Multiline → expand nested structs/arrays one element per line
                # (the previous "%type %m %r" used invalid format params, so
                #  Var.View only warned and produced an empty dump.)
                self._dbg.cmd(f'WinPrint.Var.View %TYPE %Multiline {name}')
            except Exception as e:
                return {"ok": False, "error": f"WinPrint command failed: {e}"}

        # Wait for file to be written, reading at most max_bytes.
        content = ""
        file_truncated = False
        for _ in range(30):
            if tmp.exists():
                # Wait a tiny bit for write to complete
                _time.sleep(0.05)
                try:
                    with tmp.open("r", errors="replace") as fh:
                        content = fh.read(max_bytes)
                        if fh.read(1):
                            file_truncated = True
                    break
                except Exception:
                    pass
            _time.sleep(0.05)

        try:
            tmp.unlink()
        except OSError:
            pass

        if not content:
            return {"ok": False, "error": f"Structure {name} could not be inspected or file was empty."}

        # Parse content
        parsed = self._parse_var_view(content)
        if not parsed:
            return {"ok": False, "error": "Failed to parse structure content.", "raw": content[:4000]}

        pruned = self._prune_tree(parsed, max_depth=max_depth, max_members=max_members)
        result = {"ok": True, "structure": pruned}
        if file_truncated:
            result["note"] = (
                f"Dump exceeded max_bytes ({max_bytes}); parsed a prefix only. "
                "Inspect a specific sub-member by name for the full value."
            )
        return result

    @staticmethod
    def _prune_tree(node: dict, *, max_depth: int, max_members: int, _depth: int = 0) -> dict:
        """Bound a parsed member tree to max_depth levels / max_members per node."""
        members = node.get("members")
        if members is None:
            return node
        out = {k: v for k, v in node.items() if k != "members"}
        if _depth >= max_depth:
            out["members_omitted"] = len(members)
            out["truncated"] = "max_depth"
            return out
        kept = members[:max_members]
        out["members"] = [
            T32Client._prune_tree(m, max_depth=max_depth, max_members=max_members, _depth=_depth + 1)
            for m in kept
        ]
        if len(members) > max_members:
            out["members_omitted"] = len(members) - max_members
            out["truncated"] = "max_members"
        return out

    def _parse_var_view(self, text: str) -> dict:
        def extract_type_and_name(text: str) -> tuple[str, str]:
            text = text.strip()
            if text.startswith("("):
                depth = 0
                for idx, char in enumerate(text):
                    if char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                        if depth == 0:
                            type_str = text[1:idx].strip()
                            name_str = text[idx+1:].strip()
                            return type_str, name_str
            elif text.endswith(")"):
                depth = 0
                for idx in range(len(text) - 1, -1, -1):
                    char = text[idx]
                    if char == ")":
                        depth += 1
                    elif char == "(":
                        depth -= 1
                        if depth == 0:
                            name_str = text[:idx].strip()
                            type_str = text[idx+1:-1].strip()
                            return type_str, name_str
            return "", text

        lines = text.splitlines()
        root = {"name": "root", "type": "", "value": "", "members": []}
        stack = [root]

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("B::"):
                continue

            if stripped == ")":
                if len(stack) > 1:
                    stack.pop()
                continue

            if "=" in stripped:
                name_part, value_part = stripped.split("=", 1)
                name_part = name_part.strip()
                value_part = value_part.strip()
            else:
                name_part = stripped
                value_part = ""

            is_container = False
            if value_part == "(" or value_part.endswith("("):
                is_container = True
                value_part = ""

            type_str, name_str = extract_type_and_name(name_part)

            # Strip trailing commas and handle closing parentheses in value_part
            num_pops = 0
            while value_part.endswith(")"):
                value_part = value_part[:-1].strip()
                num_pops += 1
            if value_part.endswith(","):
                value_part = value_part[:-1].strip()

            member = {
                "name": name_str,
                "type": type_str,
                "value": value_part,
            }
            if is_container:
                member["members"] = []

            stack[-1]["members"].append(member)

            if is_container:
                stack.append(member)

            for _ in range(num_pops):
                if len(stack) > 1:
                    stack.pop()

        if root["members"]:
            return root["members"][0]
        return {}

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
