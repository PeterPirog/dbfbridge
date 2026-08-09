"""dbfbridge — DBF (Visual FoxPro) → CSV/JSON/JSONL exporter.

Public API:
    from dbf_bridge.exporter.config import make_config
    from dbf_bridge.exporter.discovery import discover_tables
    from dbf_bridge.exporter.writer import export_table
    from dbf_bridge.exporter.reporting import write_reports

CLI entry points (installed via pip):
    dbf-bridge        — export DBF tree to CSV/JSON/JSONL
    dbf-bridge-verify — verify conversion integrity

A higher-level ``convert`` / ``verify`` facade is planned for a later release
(see the Roadmap section in README.md).
"""

from __future__ import annotations

from .exporter.polish_codecs import register_polish_codecs

__version__ = "0.1.0"

register_polish_codecs()

__all__ = ["__version__"]
