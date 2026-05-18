"""Smoke tests that don't need a live TRACE32 instance.

Verifies the MCP server boots, the schemas serialise, and the docs tools
return real results from whichever DB the runtime resolves to.
"""

from __future__ import annotations

import asyncio

import pytest


def test_all_tools_register():
    from trace32_mcp.server import TOOLS

    names = {t[0] for t in TOOLS}
    # Lifecycle: spawn and attach are explicit and separate.
    assert "t32_spawn" in names
    assert "t32_attach" in names
    assert "t32_shutdown" in names
    assert "t32_list_instances" in names
    assert "t32_get_log" in names
    # Core debug verbs
    assert "t32_load_program" in names
    assert "t32_run_practice" in names
    # Docs (no T32 needed)
    assert "t32_search_manuals" in names
    assert "t32_lookup_command" in names


def test_schemas_are_valid_json_schema():
    from trace32_mcp.server import TOOLS, _schema

    for name, _desc, _handler, model_cls in TOOLS:
        schema = _schema(model_cls)
        assert schema.get("type") == "object", f"{name} schema not object"
        assert "properties" in schema, f"{name} missing properties"


def test_docs_search_runs():
    from trace32_mcp.manuals.config import load_settings

    if not load_settings().db_paths:
        pytest.skip("no manuals DB available (set T32_MANUALS_DB or run `t32-rag ingest`)")

    from trace32_mcp.tools.docs import t32_search_manuals

    out = t32_search_manuals({"query": "set a breakpoint", "k": 3})
    assert out["ok"] is True
    assert len(out["hits"]) >= 1
    hit = out["hits"][0]
    assert {"doc_file", "page_start", "page_end", "section", "text"} <= hit.keys()


def test_lookup_command_runs():
    from trace32_mcp.manuals.config import load_settings

    if not load_settings().db_paths:
        pytest.skip("no manuals DB available")

    from trace32_mcp.tools.docs import t32_lookup_command

    out = t32_lookup_command({"command": "Data.LOAD.Elf"})
    assert out["ok"] is True
    assert len(out["hits"]) >= 1
