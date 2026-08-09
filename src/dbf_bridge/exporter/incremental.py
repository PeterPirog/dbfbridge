from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discovery import find_related_file
from .models import DiscoveredTable, TableResult
from .reporting import atomic_write_text
from .validation import sha256_file

CHECKSUM_MANIFEST_NAME = "conversion_checksums.json"
MANIFEST_FORMAT = "dbfbridge-conversion-checksums"
MANIFEST_VERSION = 1
OUTPUT_COMPATIBILITY_VERSION = 1


def load_checksum_manifest(output_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = output_root / CHECKSUM_MANIFEST_NAME
    if not path.is_file():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as infile:
            manifest = json.load(infile)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Cannot read {CHECKSUM_MANIFEST_NAME}: {exc}. All tables will be converted."
    if (
        not isinstance(manifest, dict)
        or manifest.get("manifest_format") != MANIFEST_FORMAT
        or manifest.get("manifest_version") != MANIFEST_VERSION
    ):
        return None, (
            f"Unsupported {CHECKSUM_MANIFEST_NAME} format or version. "
            "All tables will be converted."
        )
    return manifest, None


def source_fingerprint(
    table: DiscoveredTable,
    source_root: Path,
    *,
    known_dbf_sha256: str | None = None,
    known_fpt_sha256: str | None = None,
) -> dict[str, Any]:
    cdx_path = find_related_file(table.source_path, ".cdx")
    return {
        "dbf": _file_fingerprint(
            table.source_path,
            source_root,
            known_sha256=known_dbf_sha256,
        ),
        "fpt": _file_fingerprint(
            table.memo_path,
            source_root,
            known_sha256=known_fpt_sha256,
        ),
        "cdx": _file_fingerprint(cdx_path, source_root),
    }


def same_source(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    for role in ("dbf", "fpt", "cdx"):
        left = first.get(role)
        right = second.get(role)
        if left is None or right is None:
            if left != right:
                return False
            continue
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        for name in ("path", "size_bytes", "sha256"):
            if left.get(name) != right.get(name):
                return False
    return True


def cached_results_for_table(
    manifest: Mapping[str, Any],
    table_name: str,
    fingerprint: Mapping[str, Any],
    signature: Mapping[str, Any],
    requested_formats: list[str],
    output_root: Path,
) -> list[TableResult] | None:
    if manifest.get("signature") != signature:
        return None
    tables = manifest.get("tables")
    if not isinstance(tables, Mapping):
        return None
    entry = tables.get(table_name)
    if not isinstance(entry, Mapping):
        return None
    cached_source = entry.get("source")
    if not isinstance(cached_source, Mapping) or not same_source(cached_source, fingerprint):
        return None
    raw_results = entry.get("results")
    if not isinstance(raw_results, list):
        return None
    results_by_format = {
        str(item.get("format")): item for item in raw_results if isinstance(item, Mapping)
    }
    if any(fmt not in results_by_format for fmt in requested_formats):
        return None
    restored: list[TableResult] = []
    for fmt in requested_formats:
        raw = results_by_format[fmt]
        if not _cached_result_is_valid(raw, output_root):
            return None
        values = {
            field.name: raw[field.name]
            for field in fields(TableResult)
            if field.name in raw
        }
        values["status"] = "SKIPPED"
        values["engine"] = "incremental-cache"
        values["elapsed_seconds"] = 0.0
        values["errors"] = []
        restored.append(TableResult(**values))
    return restored


def write_checksum_manifest(
    output_root: Path,
    *,
    source_root: Path,
    signature: Mapping[str, Any],
    fingerprints: Mapping[str, Mapping[str, Any]],
    results: list[TableResult],
    requested_formats: list[str],
    dbfbridge_version: str,
) -> None:
    grouped: dict[str, list[TableResult]] = {}
    for result in results:
        grouped.setdefault(result.table, []).append(result)
    tables: dict[str, Any] = {}
    expected_formats = set(requested_formats)
    for table_name, table_results in grouped.items():
        usable = [
            result
            for result in table_results
            if result.status in {"OK", "WARNING", "SKIPPED"} and not result.errors
        ]
        if {result.format for result in usable} != expected_formats:
            continue
        fingerprint = fingerprints.get(table_name)
        if fingerprint is None:
            continue
        tables[table_name] = {
            "source": fingerprint,
            "results": [result.to_report_dict() for result in usable],
        }
    manifest = {
        "manifest_format": MANIFEST_FORMAT,
        "manifest_version": MANIFEST_VERSION,
        "output_compatibility_version": OUTPUT_COMPATIBILITY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dbfbridge_version": dbfbridge_version,
        "source_root": str(source_root.resolve()),
        "signature": dict(signature),
        "tables": tables,
    }
    atomic_write_text(
        output_root / CHECKSUM_MANIFEST_NAME,
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
    )


def _file_fingerprint(
    path: Path | None,
    source_root: Path,
    *,
    known_sha256: str | None = None,
) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    stat = path.stat()
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        relative = Path(path.name)
    return {
        "path": relative.as_posix(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": known_sha256 or sha256_file(path),
    }


def _cached_result_is_valid(result: Mapping[str, Any], output_root: Path) -> bool:
    for path_key, hash_key, size_key in (
        ("output", "sha256", "size_bytes"),
        ("schema", "schema_sha256", None),
        ("deleted_output", "deleted_sha256", None),
    ):
        value = result.get(path_key)
        if value is None:
            if path_key == "output" or result.get(hash_key) is not None:
                return False
            continue
        path = _safe_output_path(output_root, str(value))
        expected_hash = result.get(hash_key)
        if path is None or not path.is_file() or not isinstance(expected_hash, str):
            return False
        if (
            size_key is not None
            and result.get(size_key) is not None
            and path.stat().st_size != int(result[size_key])
        ):
            return False
        if sha256_file(path) != expected_hash:
            return False
    return True


def _safe_output_path(output_root: Path, relative: str) -> Path | None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    return output_root / path
