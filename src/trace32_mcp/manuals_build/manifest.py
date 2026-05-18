"""Curated list of TRACE32 PDFs that get ingested.

Scope: Cortex-M debugger work, ARM general, simulator, PRACTICE/RCL scripting,
plus the alphabetical command reference. RTOS-awareness and non-ARM arches
are intentionally excluded; add later via `T32_RAG_EXTRA_PDFS` env var or by
editing this list.
"""

from __future__ import annotations

CURATED_MANIFEST: list[dict[str, str]] = [
    # PRACTICE scripting (critical for the MCP escape hatch)
    {"file": "practice_user.pdf",      "category": "practice",  "title": "PRACTICE Script Language User's Guide"},
    {"file": "practice_ref.pdf",       "category": "practice",  "title": "PRACTICE Script Language Reference"},

    # Remote Control API — the protocol the MCP uses to drive T32
    {"file": "api_remote_c.pdf",       "category": "api",       "title": "Remote Control API (C/Python) Reference"},
    {"file": "api_apu.pdf",            "category": "api",       "title": "Auxiliary Processing Unit API"},

    # General alphabetical command reference (a..z + functions + index)
    *[
        {"file": f"general_ref_{c}.pdf", "category": "general_ref", "title": f"General Commands Reference '{c.upper()}'"}
        for c in "abcdefghijklmnopqrstuvwxyz"
    ],
    {"file": "general_func.pdf",       "category": "general",   "title": "General Function Reference"},
    {"file": "commandlist.pdf",        "category": "general",   "title": "Full Command List"},

    # IDE + install + concepts
    {"file": "ide_ref.pdf",            "category": "ide",       "title": "TRACE32 IDE Reference"},
    {"file": "installation.pdf",       "category": "ide",       "title": "TRACE32 Installation Guide"},
    {"file": "trace32_concepts.pdf",   "category": "concepts",  "title": "TRACE32 Concepts"},
    {"file": "trace32_terms.pdf",      "category": "concepts",  "title": "TRACE32 Terms and Definitions"},

    # Debugger — ARM family (incl. Cortex-M)
    {"file": "debugger_cortexm.pdf",   "category": "debugger",  "title": "Debugger Reference: ARM Cortex-M"},
    {"file": "debugger_arm.pdf",       "category": "debugger",  "title": "Debugger Reference: ARM (legacy)"},
    {"file": "debugger_armv8v9.pdf",   "category": "debugger",  "title": "Debugger Reference: ARMv8/v9"},

    # Simulator — primary use case (load AXF/BIN into sim)
    {"file": "simulator_arm.pdf",      "category": "simulator", "title": "Simulator: ARM"},
    {"file": "simulator_api.pdf",      "category": "simulator", "title": "Simulator API"},
    {"file": "virtual_targets.pdf",    "category": "simulator", "title": "Virtual Targets"},

    # App notes — ARM/Cortex-M relevant
    {"file": "app_arm_coresight.pdf",         "category": "app", "title": "App Note: ARM CoreSight"},
    {"file": "app_arm_target_interface.pdf",  "category": "app", "title": "App Note: ARM Target Interface"},
    {"file": "app_arm_mxc.pdf",               "category": "app", "title": "App Note: ARM MXC"},
    {"file": "app_jtag_interface.pdf",        "category": "app", "title": "App Note: JTAG Interface"},
    {"file": "app_code_coverage.pdf",         "category": "app", "title": "App Note: Code Coverage"},
    {"file": "app_cpp_debugging.pdf",         "category": "app", "title": "App Note: C++ Debugging"},
    {"file": "app_fdx.pdf",                   "category": "app", "title": "App Note: FDX (Fast Data eXchange)"},
    {"file": "app_logger.pdf",                "category": "app", "title": "App Note: Logger"},
    {"file": "app_iamp.pdf",                  "category": "app", "title": "App Note: AMP Debugging"},

    # Back-end integrations (GDB bridge is commonly used)
    {"file": "backend_overview.pdf",  "category": "backend", "title": "Back-End Overview"},
    {"file": "backend_gdb.pdf",       "category": "backend", "title": "Back-End: GDB Server"},

    # Training material (Cortex-M + ARM trace)
    {"file": "training_debugger.pdf",     "category": "training", "title": "Training: TRACE32 Debugger"},
    {"file": "training_cortexm_etm.pdf",  "category": "training", "title": "Training: Cortex-M ETM Trace"},
    {"file": "training_arm_etm.pdf",      "category": "training", "title": "Training: ARM ETM Trace"},
    {"file": "training_ipt_trace.pdf",    "category": "training", "title": "Training: IPT Trace"},
]


def manifest_files() -> list[str]:
    return [m["file"] for m in CURATED_MANIFEST]
