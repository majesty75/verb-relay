"""Eval, read/write memory, read/write registers."""

from __future__ import annotations

from pydantic import Field

from ._common import TargetSelector, hexdump, resolve_target


class EvalInput(TargetSelector):
    expression: str = Field(
        description="Any PRACTICE expression: symbol, Var.VALUE(x), Data.Long(0x2000), Register(R0), arithmetic, CPU(), etc. Evaluated via PYRCL's fnc() which returns the real value."
    )


class ReadMemoryInput(TargetSelector):
    address: int = Field(description="Start address (decimal or hex int)")
    length: int = Field(description="Number of bytes to read", ge=1, le=65536)
    width: int = Field(default=1, description="Element width in bytes: 1, 2, or 4 (decoded view).")
    access: str = Field(default="ANY", description="T32 access class: ANY, D, P, C, ...")


class WriteMemoryInput(TargetSelector):
    address: int = Field(description="Start address")
    data_hex: str = Field(description="Bytes to write as hex string, e.g. 'deadbeef'")
    access: str = Field(default="ANY")


class ReadRegistersInput(TargetSelector):
    group: str | None = Field(
        default=None,
        description="Optional name-prefix filter (e.g. 'R' for general purpose, 'D' for FPU doubles). Filtering is post-read; PYRCL's read_all returns every register.",
    )


class WriteRegisterInput(TargetSelector):
    name: str = Field(description="Register name, e.g. R0, PC, SP, MSP, PSR")
    value: int = Field(description="New value (decimal or hex int)")


def t32_eval(args: dict) -> dict:
    p = EvalInput(**args)
    _inst, client = resolve_target(p)
    return {"ok": True, "expression": p.expression, "result": client.eval_practice(p.expression)}


def t32_read_memory(args: dict) -> dict:
    p = ReadMemoryInput(**args)
    if p.width not in (1, 2, 4):
        return {"ok": False, "error": "width must be 1, 2, or 4"}
    _inst, client = resolve_target(p)
    try:
        data = client.read_memory(p.address, p.length, access=p.access)
    except Exception as e:
        return {"ok": False, "error": f"read_memory failed: {e}", "error_type": type(e).__name__}

    decoded: list[int] = []
    if p.width == 1:
        decoded = list(data)
    elif p.width == 2:
        for i in range(0, len(data) - 1, 2):
            decoded.append(int.from_bytes(data[i : i + 2], "little"))
    else:
        for i in range(0, len(data) - 3, 4):
            decoded.append(int.from_bytes(data[i : i + 4], "little"))

    return {
        "ok": True,
        "address": p.address,
        "length": p.length,
        "width": p.width,
        "access": p.access,
        "hex": data.hex(),
        "decoded": decoded,
        "ascii_dump": hexdump(data, p.address),
    }


def t32_write_memory(args: dict) -> dict:
    p = WriteMemoryInput(**args)
    try:
        data = bytes.fromhex(p.data_hex.replace(" ", "").replace("0x", ""))
    except ValueError as e:
        return {"ok": False, "error": f"data_hex is not valid hex: {e}"}
    _inst, client = resolve_target(p)
    try:
        client.write_memory(p.address, data, access=p.access)
    except Exception as e:
        return {"ok": False, "error": f"write_memory failed: {e}", "error_type": type(e).__name__}
    return {"ok": True, "address": p.address, "bytes_written": len(data), "access": p.access}


def t32_read_registers(args: dict) -> dict:
    p = ReadRegistersInput(**args)
    _inst, client = resolve_target(p)
    try:
        regs = client.read_registers()
    except Exception as e:
        return {"ok": False, "error": f"read_registers failed: {e}", "error_type": type(e).__name__}
    if p.group:
        prefix = p.group.upper()
        regs = [r for r in regs if (r.get("name") or "").upper().startswith(prefix)]
    # Decode values to both decimal and hex for AI readability.
    for r in regs:
        v = r.get("value")
        if isinstance(v, int):
            r["value_hex"] = f"0x{v:X}"
    return {"ok": True, "count": len(regs), "registers": regs}


def t32_write_register(args: dict) -> dict:
    p = WriteRegisterInput(**args)
    _inst, client = resolve_target(p)
    try:
        client.write_register(p.name, p.value)
    except Exception as e:
        return {"ok": False, "error": f"write_register failed: {e}", "error_type": type(e).__name__}
    # Read back for confirmation.
    try:
        readback = client.read_register(p.name)
    except Exception:
        readback = None
    return {"ok": True, "name": p.name, "wrote": p.value, "readback": readback}
