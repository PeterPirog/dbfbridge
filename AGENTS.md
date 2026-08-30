# AGENTS.md — dbfbridge project context

Read this file before changing the repository. User-facing behavior is documented in
`README.md`; this file records the implementation map and maintenance rules.

## Project

`dbfbridge` is a Python 3.10+ toolkit for Visual FoxPro DBF/FPT migration:

- DBF → CSV/JSON/JSONL/XLSX export;
- checksum-based incremental re-export;
- schema-driven CSV/JSON/JSONL/XLSX → DBF/FPT reconstruction;
- export verification and diagnostic DBF → JSONL → DBF round trips;
- cp1250/cp852/Mazovia decoding for legacy Polish data;
- typed high-level API available from both `dbfbridge` and `dbf_bridge`.

Repository: <https://github.com/PeterPirog/dbfbridge>

License: MIT

Current package version/status: 0.1.0, alpha.

## Architecture

```text
src/dbf_bridge/
├── core/                  Phase 1A direct read core (read-only, stdlib + dbfread tables)
│   ├── errors.py          ErrorCode + DirectReadError subclasses (JSON-safe to_dict)
│   ├── codecs.py          Mazovia/PIAST table + registration + driver resolution (single source)
│   ├── fields.py          pure field classification (memo/binary/supported) + type names
│   ├── header.py          single pure DBF header parser (O(header), read-only)
│   ├── models.py          FieldInfo / TableInfo / TableSchema (frozen, to_dict)
│   └── inspect.py         public inspect_table / read_schema + companion discovery
├── api.py                 stable high-level Python functions
├── api_models.py          options, progress events, and run results
├── cli.py                 export CLI and multi-format orchestration
├── converters.py          streaming JSONL → JSON/CSV/XLSX converters
├── verifier.py            exported-file verifier
├── import_cli.py          reconstruction CLI
├── quality.py             retained round-trip diagnostics
├── exporter/
│   ├── config.py          export validation
│   ├── discovery.py       recursive DBF/FPT/CDX discovery
│   ├── incremental.py     conversion_checksums.json cache
│   ├── models.py          export dataclasses and type aliases
│   ├── polish_codecs.py   Mazovia/PIAST codec
│   ├── reader.py          dbfread parser and encoding fallback
│   ├── serialization.py   JSON-safe DBF value serialization
│   ├── validation.py      output parsing and SHA-256
│   ├── writer.py          atomic DBF → JSONL/schema export
│   └── reporting.py       migration_report.jsonl/.csv
└── importer/
    ├── checksum.py        schema-aware canonical checksums
    ├── models.py          reconstruction configuration/results
    ├── readers.py         JSONL/JSON/CSV/XLSX input streams
    ├── reconstruct.py     directory-tree orchestration
    ├── writer.py          DBF/FPT creation and raw-layout restoration
    └── reporting.py       reconstruction_report.jsonl
src/dbfbridge/
└── __init__.py            recommended public import name
```

Other important paths:

- `examples/` — thin executable wrappers and PowerShell examples;
- `examples/python_api.py` — complete programmatic API example;
- `examples/inspect_table.py` — Phase 1A read-only inspection example;
- `docs/architecture/phase-1-direct-read.md` — Phase 1A direct read contract;
- `tests/test_direct_read_schema.py` — Phase 1A direct read integration tests;
- `tests/fixtures/generate_sample_dbf.py` — deterministic fixture generator;
- `tests/conftest.py` — generates fixtures in pytest temporary storage;
- `benchmarks/` — synthetic JSONL conversion benchmark;
- `.github/workflows/ci.yml` — Linux/Windows compatibility checks;
- `.github/workflows/publish.yml` — release build and PyPI Trusted Publishing;
- `PUBLISHING.md` — release checklist and one-time PyPI configuration;
- `pyproject.toml` — package metadata, runtime/dev dependencies, console scripts.

## Command-line interfaces

All real-data commands require explicit source/output paths except the verifier, whose
defaults point at generated test fixtures.

```powershell
dbf-bridge --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
dbf-bridge --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx --incremental
dbf-bridge-verify --source "K:\dbf_source" --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
dbf-bridge-import --source "K:\dbf_output" --output "K:\dbf_rebuilt" --formats jsonl --memo inline --overwrite
dbf-bridge-quality --source "K:\dbf_source" --output "K:\dbf_quality" --overwrite
```

Console entry points in `pyproject.toml` must stay synchronized with README and examples:

- `dbf-bridge` → `dbf_bridge.cli:main`;
- `dbf-bridge-verify` → `dbf_bridge.verifier:main`;
- `dbf-bridge-import` → `dbf_bridge.import_cli:main`;
- `dbf-bridge-quality` → `dbf_bridge.quality:main`.

The public Python interface must stay synchronized as well:

- `export_dbf()` → `ExportRunResult`;
- `reconstruct_dbf()` → `ReconstructionRunResult`;
- `verify_conversion()` → `VerificationRunResult`;
- `check_conversion_quality()` → `QualityRunResult`;
- `inspect_table()` → `TableInfo` (Phase 1A, read-only);
- `read_schema()` → `TableSchema` (Phase 1A, read-only).

Phase 1A direct read core (`src/dbf_bridge/core/`) is a hard boundary:
no CLI, no reporting, no output files, no `.partial` artifacts, no Polars/
OpenPyXL/XlsxWriter/orjson/`dbf`, no network/COM/VFP, no printing or
`sys.exit`. It runs in O(header) work and never iterates the record area.
The exporter delegates its header parse to `core.header.parse_header` and its
Mazovia table to `core.codecs` — there is exactly one header parser and one
codepage table in the codebase. `import dbfbridge` must register no codepage,
create no files, and load no CLI/reporting or heavy dependency.

Use `from dbfbridge import ...` in user documentation. `dbf_bridge` exposes the same
symbols for compatibility. CLI modules should delegate to these functions wherever
possible instead of creating a second behavior path.

## Data and reconstruction rules

- DBF is read with `dbfread` in streaming mode and written with `dbf`.
- The exporter always creates an internal JSONL representation; requested JSON, CSV,
  and XLSX outputs are streamed from it.
- JSONL is the preferred reconstruction format. CSV omits memo by default and spreadsheet
  formats are not intended to preserve every raw DBF byte.
- `<table>_schema.json` is the authority for field descriptors, header/codepage details,
  memo layout, and original checksums.
- JSON/JSONL carries reserved raw-record metadata used for exact layout restoration.
- `--deleted include` is required when deleted record order matters for raw identity.
- CDX tag expressions are not stored in DBF field metadata and cannot be reconstructed.
- `raw_*_match` proves byte identity; canonical checksums prove schema-aware value
  equivalence. Canonical equality does not imply byte equality.
- Atomic writes use a sibling `.partial` file, flush/fsync, and `os.replace`.
- `conversion_checksums.json` skips a table only after source, settings, schema, and every
  requested output are revalidated. It never deletes stale output files automatically.

## Dependencies

Runtime dependencies are installed by default:

- `dbfread` — DBF/FPT reading;
- `dbf` — DBF/FPT reconstruction and fixture generation;
- `orjson` — JSONL parsing/validation;
- `polars` — streaming CSV conversion;
- `xlsxwriter` — constant-memory XLSX writing;
- `openpyxl` — read-only XLSX reconstruction.

The empty `import` and `xlsx` extras are compatibility aliases. Development setup is:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Validation before publishing

Run from a clean checkout; tests generate their own fixture files:

```powershell
pytest
ruff check src tests benchmarks examples
python -m build
twine check dist/*
python -m dbf_bridge.cli --help
python -m dbf_bridge.import_cli --help
python -m dbf_bridge.verifier --help
python -m dbf_bridge.quality --help
```

Before a release, also follow `PUBLISHING.md`. A GitHub release tag must exactly match
`v<project.version>`; `publish.yml` rejects mismatches before uploading immutable PyPI
artifacts.

Also inspect `git diff --check` and do not commit generated DBF/FPT/CDX, conversion
outputs, reports, `build/`, `dist/`, virtual environments, or user data.

## Code conventions

- Use `from __future__ import annotations` and complete type hints.
- Keep large-data paths streaming and bounded-memory.
- Preserve relative paths and filename casing.
- Never silently weaken checksum, atomic-write, encoding, or reconstruction guarantees.
- Add a regression test for every fixed defect.
- Keep README, examples, `--help`, changelog, and `pyproject.toml` consistent whenever
  behavior, options, defaults, entry points, or dependencies change.
- User-facing reports must include enough context to diagnose a failed table without the
  original Python traceback.
- Public API functions are silent by default, accept `str` and `PathLike`, return typed
  run results, and expose structured progress through `ProgressEvent` callbacks.
- Keep `src/dbfbridge/__init__.py`, `src/dbf_bridge/__init__.py`, their `__all__` lists,
  type markers, README API tables, and API tests synchronized.

## Known follow-up work

- index-aware CDX reconstruction, if a reliable source of tag definitions is added.
