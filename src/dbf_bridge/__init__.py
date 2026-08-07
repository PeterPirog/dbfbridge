"""dbfbridge — bidirectional DBF (Visual FoxPro) ↔ CSV/JSON/JSONL/XLSX converter.

Public API:
    from dbf_bridge import convert, verify

CLI entry points (installed via pip):
    dbf-bridge        — export DBF tree to CSV/JSON/JSONL/XLSX
    dbf-bridge-verify — verify conversion integrity
"""

from __future__ import annotations

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]