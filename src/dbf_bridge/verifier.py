"""
dbf_bridge.verifier
====================

Weryfikuje poprawność konwersji plików DBF (wraz z FPT/CDX) do formatów
pośrednich CSV / JSON / JSONL / XLSX. Skrypt sprawdza:
  1. Kompletność plików (każdy DBF ma odpowiednik w każdym formacie).
  2. Liczbę rekordów (CSV/JSON/JSONL/XLSX vs DBF).
  3. SHA-256 plików wyjściowych vs raport migracji.
  4. Zgodność schema (nazwy pól, typy, długości).
  5. Poprawność składniową (CSV, JSON, JSONL, XLSX).
  6. Obecność FPT/CDX.

Punkt wejścia: dbf-bridge-verify
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dbfread import DBF
from dbfread.codepages import guess_encoding

from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.models import DiscoveredTable

DEFAULTS = {
    "formats": "csv,json,jsonl,xlsx",
    "verbose": True,
    "strict": True,
}

ALL_FORMATS: tuple[str, ...] = ("csv", "json", "jsonl", "xlsx")


@dataclass
class FileCheck:
    relative_path: str
    exists: bool = False
    size_bytes: int = 0
    sha256: str | None = None
    record_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TableCheck:
    dbf_relative: str
    output_base: str
    formats: dict[str, FileCheck] = field(default_factory=dict)
    schema: FileCheck | None = None
    dbf_record_count: int = 0
    dbf_deleted_count: int = 0
    dbf_fields: list[dict[str, Any]] = field(default_factory=list)
    dbf_codepage: str | None = None
    dbf_version: str | None = None
    has_fpt: bool = False
    has_cdx: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "FAILED"
        if self.warnings:
            return "WARNING"
        return "OK"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_jsonl_records(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    with path.open("r", encoding="utf-8", newline="") as infile:
        for line_number, line in enumerate(infile, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    errors.append(f"line {line_number}: not a JSON object")
                    continue
                count += 1
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: {exc.msg}")
    return count, errors


def count_json_records(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    opened = False
    closed = False
    with path.open("r", encoding="utf-8", newline="") as infile:
        for line_number, line in enumerate(infile, start=1):
            text = line.strip()
            if not text:
                continue
            if not opened:
                if text != "[":
                    return 0, ["JSON top-level is not an array"]
                opened = True
                continue
            if text == "]":
                closed = True
                continue
            if closed:
                errors.append(f"line {line_number}: content after JSON array")
                continue
            payload = text[:-1] if text.endswith(",") else text
            try:
                item = json.loads(payload)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: {exc.msg}")
                continue
            count += 1
            if not isinstance(item, dict):
                errors.append(f"element {count}: not a JSON object")
    if not opened or not closed:
        errors.append("JSON array is incomplete")
    return count, errors


def count_csv_records(path: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    count = 0
    with path.open("r", encoding="utf-8", newline="") as infile:
        reader = csv.reader(infile)
        try:
            header = next(reader)
        except StopIteration:
            return 0, ["empty CSV (no header)"]
        for row_number, row in enumerate(reader, start=2):
            count += 1
            if len(row) != len(header):
                errors.append(f"row {row_number}: {len(row)} cols, expected {len(header)}")
    return count, errors


def count_xlsx_records(path: Path) -> tuple[int, list[str]]:
    """Count rows in all ``Dane_*`` worksheets without loading the workbook."""
    errors: list[str] = []
    records = 0
    try:
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel_targets = {
                rel.attrib["Id"]: rel.attrib["Target"]
                for rel in relationships
                if "Id" in rel.attrib and "Target" in rel.attrib
            }
            relationship_key = (
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            sheets = [
                (sheet.attrib.get("name", ""), sheet.attrib.get(relationship_key))
                for sheet in workbook.iter()
                if sheet.tag.endswith("}sheet")
            ]
            data_sheets = [(name, rel_id) for name, rel_id in sheets if name.startswith("Dane_")]
            if not data_sheets:
                return 0, ["XLSX has no Dane_* worksheet"]

            for name, rel_id in data_sheets:
                target = rel_targets.get(rel_id or "")
                if target is None:
                    errors.append(f"worksheet {name!r} has no relationship target")
                    continue
                member = target.lstrip("/")
                if not member.startswith("xl/"):
                    member = posixpath.normpath(posixpath.join("xl", member))
                last_row_number = 0
                with archive.open(member) as worksheet_xml:
                    for _, element in ET.iterparse(worksheet_xml, events=("end",)):
                        if element.tag.endswith("}row"):
                            last_row_number = max(
                                last_row_number,
                                int(element.attrib.get("r", last_row_number + 1)),
                            )
                        element.clear()
                records += max(0, last_row_number - 1)
    except (KeyError, OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        errors.append(f"invalid XLSX: {exc}")
    return records, errors


def read_dbf_stats(dbf_path: Path) -> tuple[int, int, list[dict[str, Any]], str | None, str | None]:
    table = DBF(dbf_path, load=False, ignore_missing_memofile=True)
    fields: list[dict[str, Any]] = []
    for f in table.fields:
        fields.append(
            {
                "name": f.name,
                "dbf_type": f.type,
                "length": f.length,
                "decimal_count": f.decimal_count,
            }
        )
    active = len(table)
    deleted = len(table.deleted) if hasattr(table, "deleted") else 0
    try:
        codepage_name = guess_encoding(table.header.language_driver)
    except Exception:
        codepage_name = None
    version = getattr(table, "dbversion", None)
    return active, deleted, fields, codepage_name, version


def find_related_file(dbf_path: Path, extension: str) -> Path | None:
    wanted = extension.lower()
    for candidate in dbf_path.parent.iterdir():
        if (
            candidate.is_file()
            and candidate.stem.lower() == dbf_path.stem.lower()
            and candidate.suffix.lower() == wanted
        ):
            return candidate
    return None


def load_schema(schema_path: Path) -> dict[str, Any] | None:
    if not schema_path.is_file():
        return None
    with schema_path.open("r", encoding="utf-8") as infile:
        try:
            return json.load(infile)
        except json.JSONDecodeError:
            return None


def load_migration_report(output_dir: Path) -> dict[tuple[str, str], dict[str, Any]] | None:
    """Wczytuje migration_report.jsonl i indeksuje po (table, format).

    Zwraca słownik {(relative_dbf, format): entry} lub None gdy plik nie istnieje.
    Każdy wpis raportu (type=="table") zawiera klucz ``format`` dodany w 0.1.0,
    dzięki czemu jeden plik raportu może opisywać wiele formatów bez nadpisywania.
    """
    report_path = output_dir / "migration_report.jsonl"
    if not report_path.is_file():
        return None
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    with report_path.open("r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "table":
                continue
            rel = entry.get("table")
            fmt = entry.get("format")
            if rel and fmt:
                tables[(rel, fmt)] = entry
    return tables


def verify_table(
    discovered: DiscoveredTable,
    source_root: Path,
    output_root: Path,
    formats: list[str],
    migration_report: dict[tuple[str, str], dict[str, Any]] | None,
    verbose: bool,
) -> TableCheck:
    rel = discovered.relative_path.as_posix()
    base_rel = discovered.relative_path.with_suffix("")
    check = TableCheck(dbf_relative=rel, output_base=base_rel.as_posix())

    try:
        active, deleted, fields, codepage, version = read_dbf_stats(discovered.source_path)
        check.dbf_record_count = active
        check.dbf_deleted_count = deleted
        check.dbf_fields = fields
        check.dbf_codepage = codepage
        check.dbf_version = version
    except Exception as exc:
        check.errors.append(f"DBF read failed: {exc}")
        return check

    check.has_fpt = discovered.memo_present
    check.has_cdx = find_related_file(discovered.source_path, ".cdx") is not None
    if not check.has_fpt and any(f["dbf_type"] == "M" for f in fields):
        check.warnings.append("DBF has M fields but no FPT memo file")
    if check.has_cdx:
        check.warnings.append("CDX index file present (not converted — informational)")

    schema_path = output_root / base_rel.with_name(f"{base_rel.name}_schema.json")
    schema_check = FileCheck(relative_path=schema_path.relative_to(output_root).as_posix())
    if schema_path.is_file():
        schema_check.exists = True
        schema_check.size_bytes = schema_path.stat().st_size
        schema_check.sha256 = sha256_file(schema_path)
        schema = load_schema(schema_path)
        if schema is None:
            schema_check.errors.append("schema is not valid JSON")
        else:
            schema_fields = schema.get("fields", [])
            if len(schema_fields) != len(fields):
                schema_check.errors.append(
                    f"schema field count {len(schema_fields)} != DBF {len(fields)}"
                )
            for sf, df in zip(schema_fields, fields, strict=False):
                if sf.get("name") != df["name"]:
                    schema_check.errors.append(
                        f"schema field name {sf.get('name')!r} != DBF {df['name']!r}"
                    )
                if sf.get("dbf_type") != df["dbf_type"]:
                    schema_check.errors.append(
                        f"schema field type {sf.get('dbf_type')!r} != DBF {df['dbf_type']!r}"
                    )
                if sf.get("length") != df["length"]:
                    schema_check.errors.append(
                        f"schema field length {sf.get('length')} != DBF {df['length']}"
                    )
    else:
        schema_check.errors.append("schema file missing")
    check.schema = schema_check

    for fmt in formats:
        out_path = output_root / base_rel.with_suffix(f".{fmt}")
        fc = FileCheck(relative_path=out_path.relative_to(output_root).as_posix())
        report_entry = migration_report.get((rel, fmt)) if migration_report is not None else None
        if report_entry is not None and report_entry.get("status") in {"FAILED", "UNSUPPORTED"}:
            report_errors = report_entry.get("errors") or []
            fc.errors.append(
                f"migration report status is {report_entry.get('status')}: "
                f"{' | '.join(str(error) for error in report_errors)}"
            )
        if not out_path.is_file():
            fc.errors.append(f"{fmt} output missing")
            check.formats[fmt] = fc
            continue
        fc.exists = True
        fc.size_bytes = out_path.stat().st_size
        fc.sha256 = sha256_file(out_path)

        if fmt == "csv":
            fc.record_count, errs = count_csv_records(out_path)
        elif fmt == "jsonl":
            fc.record_count, errs = count_jsonl_records(out_path)
        elif fmt == "json":
            fc.record_count, errs = count_json_records(out_path)
        elif fmt == "xlsx":
            fc.record_count, errs = count_xlsx_records(out_path)
        else:
            errs = [f"unknown format {fmt}"]
            fc.record_count = 0
        fc.errors.extend(errs)

        expected = check.dbf_record_count
        if fc.record_count != expected:
            fc.errors.append(f"record count {fc.record_count} != DBF active {expected}")

        if migration_report is not None:
            if report_entry is None:
                fc.warnings.append(f"no migration_report entry for ({rel}, {fmt})")
            else:
                expected_sha = report_entry.get("sha256")
                if expected_sha and fc.sha256 and expected_sha != fc.sha256:
                    fc.errors.append(
                        f"SHA-256 mismatch with migration_report "
                        f"(report={expected_sha[:12]}…, file={fc.sha256[:12]}…)"
                    )

        check.formats[fmt] = fc
        if verbose:
            print(
                f"  [{fmt:5}] {fc.relative_path}: "
                f"{fc.record_count} rekordów, {fc.size_bytes} B, "
                f"sha={fc.sha256[:12] if fc.sha256 else 'N/A'}…"
            )

    for fmt, fc in check.formats.items():
        check.errors.extend(f"[{fmt}] {e}" for e in fc.errors)
        check.warnings.extend(f"[{fmt}] {w}" for w in fc.warnings)
    if check.schema:
        check.errors.extend(f"[schema] {e}" for e in check.schema.errors)
        check.warnings.extend(f"[schema] {w}" for w in check.schema.warnings)

    return check


def verify_all(
    source_dir: Path,
    output_dir: Path,
    formats: list[str],
    verbose: bool,
) -> tuple[list[TableCheck], list[str]]:
    tables = discover_tables(source_dir)
    global_errors: list[str] = []
    if not tables:
        global_errors.append(f"no DBF files found in {source_dir}")
        return [], global_errors

    if not output_dir.is_dir():
        global_errors.append(f"output directory does not exist: {output_dir}")
        return [], global_errors

    migration_report = load_migration_report(output_dir)
    if migration_report is None:
        global_errors.append("migration_report.jsonl missing in output directory")

    checks: list[TableCheck] = []
    for discovered in tables:
        if verbose:
            print(f"\n[verify] Weryfikacja: {discovered.relative_path.as_posix()}")
        check = verify_table(discovered, source_dir, output_dir, formats, migration_report, verbose)
        checks.append(check)
        if check.status == "FAILED":
            global_errors.append(f"{check.dbf_relative}: FAILED")
    return checks, global_errors


def summarize(checks: list[TableCheck], global_errors: list[str]) -> dict[str, Any]:
    ok = sum(1 for c in checks if c.status == "OK")
    warning = sum(1 for c in checks if c.status == "WARNING")
    failed = sum(1 for c in checks if c.status == "FAILED")
    total_records_dbf = sum(c.dbf_record_count for c in checks)
    total_records_out = {
        fmt: sum(c.formats[fmt].record_count for c in checks if fmt in c.formats)
        for fmt in ALL_FORMATS
    }
    return {
        "tables": len(checks),
        "ok": ok,
        "warning": warning,
        "failed": failed,
        "total_records_dbf": total_records_dbf,
        "total_records_out": total_records_out,
        "global_errors": global_errors,
        "checks": [
            {
                "dbf": c.dbf_relative,
                "status": c.status,
                "dbf_records": c.dbf_record_count,
                "dbf_deleted": c.dbf_deleted_count,
                "has_fpt": c.has_fpt,
                "has_cdx": c.has_cdx,
                "codepage": c.dbf_codepage,
                "version": c.dbf_version,
                "formats": {
                    fmt: {
                        "exists": fc.exists,
                        "records": fc.record_count,
                        "size_bytes": fc.size_bytes,
                        "sha256": fc.sha256,
                        "errors": fc.errors,
                        "warnings": fc.warnings,
                    }
                    for fmt, fc in c.formats.items()
                },
                "schema_errors": c.schema.errors if c.schema else [],
                "errors": c.errors,
                "warnings": c.warnings,
            }
            for c in checks
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-bridge-verify",
        description="Weryfikuje poprawność konwersji DBF -> CSV/JSON/JSONL/XLSX.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Katalog źródłowy DBF (wymagany).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Katalog wyników eksportu (wymagany).",
    )
    parser.add_argument(
        "--formats",
        default=DEFAULTS["formats"],
        help=f"Lista formatów do weryfikacji (domyślnie: {DEFAULTS['formats']}).",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=DEFAULTS["verbose"],
        help="Wypisuj szczegóły per plik (domyślnie: włączone).",
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        default=DEFAULTS["strict"],
        help="Ostrzeżenia nie powodują kodu wyjścia 2.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Ścieżka do raportu JSON (domyślnie: <output>/verification_report.json).",
    )
    return parser


def _resolve_formats(formats_arg: str) -> list[str]:
    requested = [f.strip().lower() for f in formats_arg.split(",") if f.strip()]
    if not requested:
        return list(ALL_FORMATS)
    invalid = [f for f in requested if f not in ALL_FORMATS]
    if invalid:
        raise ValueError(f"Nieobsługiwany format(y): {invalid}. Dostępne: {list(ALL_FORMATS)}")
    seen: set[str] = set()
    ordered: list[str] = []
    for f in requested:
        if f not in seen:
            ordered.append(f)
            seen.add(f)
    return ordered


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"[verify] Błąd: katalog źródłowy nie istnieje: {args.source}", file=sys.stderr)
        return 1
    if not args.output.is_dir():
        print(f"[verify] Błąd: katalog wyjściowy nie istnieje: {args.output}", file=sys.stderr)
        return 1

    try:
        formats = _resolve_formats(args.formats)
    except ValueError as exc:
        print(f"[verify] Błąd: {exc}", file=sys.stderr)
        return 1

    print(f"[verify] Source:  {args.source}")
    print(f"[verify] Output:  {args.output}")
    print(f"[verify] Formats: {', '.join(formats)}")
    print(f"[verify] Strict:  {args.strict}")
    print()

    from dbf_bridge import verify_conversion

    run = verify_conversion(
        args.source,
        args.output,
        formats=formats,
        strict=args.strict,
        report=args.report,
        verbose=args.verbose,
    )
    checks = list(run.checks)
    global_errors = list(run.global_errors)
    summary = run.summary

    print("\n" + "=" * 70)
    print("[verify] Podsumowanie weryfikacji")
    print("=" * 70)
    print(f"  Tabele DBF:     {summary['tables']}")
    print(f"  OK:             {summary['ok']}")
    print(f"  Ostrzeżenia:    {summary['warning']}")
    print(f"  Błędy:          {summary['failed']}")
    print(f"  Rekordy w DBF:  {summary['total_records_dbf']}")
    for fmt, count in summary["total_records_out"].items():
        if fmt in formats:
            mark = "OK" if count == summary["total_records_dbf"] else "MISMATCH"
            print(f"  Rekordy {fmt:5}: {count}  [{mark}]")

    if global_errors:
        print("\n  Błędy globalne:")
        for e in global_errors:
            print(f"    - {e}")

    failed_tables = [c for c in checks if c.status == "FAILED"]
    if failed_tables:
        print("\n  Nieudane tabele:")
        for c in failed_tables:
            print(f"    - {c.dbf_relative}:")
            for err in c.errors[:5]:
                print(f"        {err}")

    warned_tables = [c for c in checks if c.status == "WARNING"]
    if warned_tables:
        print("\n  Tabele z ostrzeżeniami:")
        for c in warned_tables:
            print(f"    - {c.dbf_relative}:")
            for w in c.warnings[:3]:
                print(f"        {w}")

    report_path = run.report_path
    print(f"\n[verify] Raport: {report_path}")
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())
