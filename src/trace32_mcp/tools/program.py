"""Program loading + reset."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator

from ._common import TargetSelector, resolve_target


class LoadProgramInput(TargetSelector):
    axf_path: str = Field(description="Path to the ELF/AXF file with symbols")
    bin_path: str | None = Field(default=None, description="Optional raw binary loaded to base_addr")
    base_addr: int | None = Field(default=None, description="Base address for bin_path (required if bin_path given)")
    set_pc: bool = Field(default=True, description="Set PC to the entry point after load")
    reset_first: bool = Field(default=False, description="Run SYStem.RESet before loading")

    @field_validator("axf_path")
    @classmethod
    def _axf_exists(cls, v: str) -> str:
        if not Path(v).expanduser().exists():
            raise ValueError(f"axf_path does not exist: {v}")
        return str(Path(v).expanduser().resolve())


class ResetInput(TargetSelector):
    mode: str = Field(
        default="Up",
        description="One of: Up, Go, Attach, Down, NoDebug, StandBy. Maps to SYStem.Mode <mode>.",
    )
    system_reset: bool = Field(default=True, description="Also issue SYStem.RESet before changing mode")


_VALID_MODES = {"Up", "Go", "Attach", "Down", "NoDebug", "StandBy"}


def t32_load_program(args: dict) -> dict:
    p = LoadProgramInput(**args)
    _inst, client = resolve_target(p)
    steps: list[dict] = []

    if p.reset_first:
        steps.append(client.run("SYStem.RESet").to_dict())

    elf_cmd = f'Data.LOAD.Elf "{p.axf_path}"'
    if not p.set_pc:
        elf_cmd += " /NoCODE /NoREG"
    steps.append(client.run(elf_cmd).to_dict())

    if p.bin_path:
        if p.base_addr is None:
            return {"ok": False, "error": "bin_path requires base_addr", "steps": steps}
        bin_resolved = str(Path(p.bin_path).expanduser().resolve())
        steps.append(client.run(f'Data.LOAD.Binary "{bin_resolved}" 0x{p.base_addr:X}').to_dict())

    ok = all(s["ok"] for s in steps)
    return {"ok": ok, "steps": steps}


def t32_reset(args: dict) -> dict:
    p = ResetInput(**args)
    if p.mode not in _VALID_MODES:
        return {"ok": False, "error": f"mode must be one of {sorted(_VALID_MODES)}, got {p.mode}"}
    _inst, client = resolve_target(p)
    steps: list[dict] = []
    if p.system_reset:
        steps.append(client.run("SYStem.RESet").to_dict())
    steps.append(client.run(f"SYStem.Mode {p.mode}").to_dict())
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "target": client.state()}
