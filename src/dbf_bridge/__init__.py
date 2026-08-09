"""Visual FoxPro DBF migration, reconstruction, and verification toolkit.

Public API:
    from dbf_bridge.exporter.config import make_config
    from dbf_bridge.exporter.discovery import discover_tables
    from dbf_bridge.exporter.writer import export_table
    from dbf_bridge.exporter.reporting import write_reports

CLI entry points (installed via pip):
    dbf-bridge         — export DBF trees to CSV/JSON/JSONL/XLSX
    dbf-bridge-verify  — verify export integrity
    dbf-bridge-import  — reconstruct DBF/FPT trees from one exported format
    dbf-bridge-quality — run a retained diagnostic round trip

The command-line interfaces are the stable interface in 0.1.0. A higher-level
``convert`` / ``verify`` facade is planned for a later release.
"""

from __future__ import annotations

from .exporter.polish_codecs import register_polish_codecs

__version__ = "0.1.0"

register_polish_codecs()

__all__ = ["__version__"]
