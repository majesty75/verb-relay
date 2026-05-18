"""Runtime: search the TRACE32 manuals vector DB.

Heavy ingest deps (PyMuPDF, click, tqdm) live in trace32_mcp.manuals_build
and are only installed via the [build] extra.
"""
from .search import lookup_command, search_manuals

__all__ = ["search_manuals", "lookup_command"]
