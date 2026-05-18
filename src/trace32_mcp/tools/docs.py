"""Docs tools: vector search + alphabetical command lookup.

These do NOT require a T32 connection. The DB is auto-downloaded on first use
if not already present (from a configurable GitHub Release).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchManualsInput(BaseModel):
    query: str = Field(description="Natural-language query, e.g. 'how do I set a hardware breakpoint on Cortex-M'")
    k: int = Field(default=6, ge=1, le=30)
    doc_filter: list[str] = Field(default_factory=list, description="Restrict to specific PDF filenames")
    category_filter: list[str] = Field(default_factory=list, description="Restrict to categories (practice, api, general_ref, debugger, simulator, ...)")


class LookupCommandInput(BaseModel):
    command: str = Field(description="PRACTICE command name, e.g. 'Data.LOAD.Elf' or 'Break.Set'")


def t32_search_manuals(args: dict) -> dict:
    from ..manuals.search import search_manuals  # lazy: keeps cold-start cheap

    p = SearchManualsInput(**args)
    hits = search_manuals(
        p.query,
        k=p.k,
        doc_filter=p.doc_filter or None,
        category_filter=p.category_filter or None,
    )
    return {"ok": True, "query": p.query, "hits": hits}


def t32_lookup_command(args: dict) -> dict:
    from ..manuals.search import lookup_command

    p = LookupCommandInput(**args)
    hits = lookup_command(p.command)
    return {"ok": True, "command": p.command, "hits": hits}
