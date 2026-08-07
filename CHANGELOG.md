# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project structure extracted from `Logis-converters`.
- `dbf_bridge.exporter` — streaming, atomic DBF → CSV/JSON/JSONL export with:
  - SHA-256 validation and round-trip verification
  - Migration reports (`migration_report.jsonl` + `.csv`)
  - Schema files (`.schema.jsonl`) preserving DBF field types, lengths, codepage
  - Memo field policies: `skip` (null in CSV), `inline` (full text in JSON/JSONL), `null`
  - Deleted record policies: `skip`, `separate`, `include`
- `dbf_bridge.exporter.polish_codecs` — Mazovia/PIAST Polish OEM codepage registration
  with automatic fallback chain: cp1250 → cp852 → Mazovia
- `dbf_bridge.cli` — `dbf-bridge` CLI entry point (export)
- `dbf_bridge.verifier` — `dbf-bridge-verify` CLI entry point (verification)
- `tests/fixtures/generate_sample_dbf.py` — synthetic DBF generator for testing

### Pending (planned for 0.2.0+)
- Round-trip import: CSV/JSON/JSONL → DBF (with FPT memo creation)
- XLSX format support (via openpyxl)
- Python API: `from dbf_bridge import convert, verify`
- Comprehensive test suite (pytest)
- GitHub Actions CI (Python 3.10/3.11/3.12)
- PyPI publication