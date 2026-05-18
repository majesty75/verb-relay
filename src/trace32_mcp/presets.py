"""Curated TRACE32 target presets.

Weaker LLMs make a lot of mistakes when composing PRACTICE setup scripts
from scratch — wrong `SYStem.CPU` name, missing `SYStem.MemAccess`, putting
runtime commands into config.t32, etc. The preset library lets a model pick
a known-good configuration by name (e.g. "cortexm3-sim") and get back a
ready-to-go `startup_script` body, without having to write any PRACTICE.

Each preset declares:
  * `arch`        — what t32_spawn's `arch` field should be set to
  * `backend`     — sim / usb / net / usb_proxy / custom (default 'sim')
  * `startup_script` — PRACTICE body that runs after PowerView boots
  * `notes`       — one-line context for the AI

The selection criteria:
  * Cover the chips most commonly used in the field
  * Sim-friendly entries first (no hardware required to validate)
  * Real-hardware entries clearly tagged with their backend requirements
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetPreset:
    name: str
    arch: str
    backend: str
    startup_script: str
    notes: str

    def to_dict(self) -> dict:
        return {
            "name": self.name, "arch": self.arch, "backend": self.backend,
            "startup_script": self.startup_script.strip(),
            "notes": self.notes,
        }


# All entries here have been hand-verified or matched against bundled
# Lauterbach demo scripts. Add new ones as needed.
_PRESETS: list[TargetPreset] = [
    # --- ARM / Cortex-M simulator (most common starter target) ---
    TargetPreset(
        name="cortexm3-sim",
        arch="cortexm", backend="sim",
        startup_script="""
SYStem.CPU CORTEXM3
SYStem.Up
""",
        notes="Generic Cortex-M3 in simulator. Free, no license needed. Good default for `Hello world` AXFs.",
    ),
    TargetPreset(
        name="cortexm4-sim",
        arch="cortexm", backend="sim",
        startup_script="""
SYStem.CPU CORTEXM4
SYStem.Up
""",
        notes="Generic Cortex-M4 (FPv4-SP) in simulator.",
    ),
    TargetPreset(
        name="cortexm7-sim",
        arch="cortexm", backend="sim",
        startup_script="""
SYStem.CPU CORTEXM7
SYStem.Up
""",
        notes="Generic Cortex-M7 (FPv5-SP/DP) in simulator.",
    ),
    TargetPreset(
        name="cortexm33-sim",
        arch="cortexm", backend="sim",
        startup_script="""
SYStem.CPU CORTEXM33
SYStem.Up
""",
        notes="Cortex-M33 (Armv8-M Mainline + TrustZone). Sim build only — TrustZone setup minimal.",
    ),
    TargetPreset(
        name="cortexa53-sim",
        arch="cortexa", backend="sim",
        startup_script="""
SYStem.CPU CORTEXA53
SYStem.Up
""",
        notes="Cortex-A53 (AArch64) sim. Useful for Linux kernel debug warmups.",
    ),

    # --- ARM / Cortex-M hardware via PowerDebug ---
    TargetPreset(
        name="stm32h743-swd",
        arch="cortexm", backend="usb",
        startup_script="""
SYStem.CPU STM32H743ZI
SYStem.CONFIG.DEBUGPORTTYPE SWD
SYStem.MemAccess DAP
SYStem.Up
""",
        notes="STM32H7 single-core via SWD on USB PowerDebug. Add `target_node=` to t32_spawn if you have multiple probes.",
    ),
    TargetPreset(
        name="nrf52-swd",
        arch="cortexm", backend="usb",
        startup_script="""
SYStem.CPU NRF52840
SYStem.CONFIG.DEBUGPORTTYPE SWD
SYStem.MemAccess DAP
SYStem.Up
""",
        notes="Nordic nRF52840 (Cortex-M4F) via SWD.",
    ),
    TargetPreset(
        name="rp2040-swd",
        arch="cortexm", backend="usb",
        startup_script="""
SYStem.CPU RP2040
SYStem.CONFIG.DEBUGPORTTYPE SWD
SYStem.MemAccess DAP
SYStem.Up
""",
        notes="Raspberry Pi RP2040 dual Cortex-M0+ via SWD. Default core 0; use `SYStem.CONFIG.CORE` to switch.",
    ),

    # --- PowerPC / RISC-V / TriCore (broader architectures) ---
    TargetPreset(
        name="riscv-sim",
        arch="riscv", backend="sim",
        startup_script="""
SYStem.CPU RV32G
SYStem.Up
""",
        notes="Generic RV32G simulator.",
    ),
    TargetPreset(
        name="tc397-sim",
        arch="tricore", backend="sim",
        startup_script="""
SYStem.CPU TC397XE
SYStem.Up
""",
        notes="Infineon AURIX TC397XE (TriCore) simulator. Real hardware uses backend='usb' or 'net'.",
    ),
]


def list_presets() -> list[dict]:
    return [p.to_dict() for p in _PRESETS]


def get_preset(name: str) -> TargetPreset | None:
    name = (name or "").lower()
    for p in _PRESETS:
        if p.name.lower() == name:
            return p
    return None
