"""The separate v1.1 Direct Write measured benchmark profile (DBFB-PERF-001..006).

This module is the FIRST bounded Phase F deliverable: a standalone, versioned
Direct Write benchmark contract.  It measures the PUBLIC v1.1 contract
(``from dbfbridge import write_table``) and validates outputs with public
Direct Read APIs.  It reuses the existing :mod:`benchmarks.metrics`
machinery (``RssSampler`` / ``AtomicPublishTracker`` / ``run``) — no second
metrics framework is created.

Contract identity
-----------------
``dbfbridge-direct-write-v1`` — deliberately distinct from
``phase-1-direct-read-v1`` / ``phase-3-performance-v1`` so Direct Write
evidence can never be confused with historical Phase 3 migration/read
evidence (DBFB-PERF-002).  The first profile is MEASURED EVIDENCE, not a
regression baseline (DBFB-PERF-006): the artifact validator enforces only
structural/correctness gates (zero intermediate JSONL, zero residue, correct
counts, one-shot input), never performance thresholds.

Temporary byte model (DBFB-STREAM-006/DBFB-COST-002)
----------------------------------------------------
Three DISTINCT counters, never merged or derived from one another:

- ``temporary_publish_bytes_written`` — logical size of the atomic
  ``.partial`` publication files at ``os.replace`` time (existing
  ``AtomicPublishTracker``);
- ``private_spool_bytes_written`` — logical size of the Direct Write private
  ``.direct-write.spool`` files, OBSERVED at unlink time inside the
  scenario-local staging directory supplied through the PUBLIC
  ``write_table(staging_directory=...)`` parameter (benchmark-only
  interception; no production instrumentation, no private imports);
- ``temporary_bytes_written`` = publish + spool (the architecture metric);
  ``null`` + reason when the instrumentation is incomplete.

``temporary_bytes_left`` counts ALL staging residues (``.partial``,
``.publish-backup``, ``.direct-write.spool``) after a handled run — 0 for
every completed scenario.  ``intermediate_jsonl_bytes`` is the explicit
constant 0 (Direct Write has no JSONL transport); the validator rejects any
non-zero value.

Scenarios in this bounded task
------------------------------
- ``direct_write_190k_flat`` (W1) — the plain flat path;
- ``direct_write_1m_flat`` (W3) — DBFB-PERF-004 bounded-input evidence from a
  lazy generator (the full input is NEVER materialized);
- ``direct_write_varchar_nullflags`` (W10) — the private bounded replay/spool
  path (full profile spills to disk and reports measured spool bytes);
- ``cancellation_cleanup_smoke`` (W12) — FUNCTIONAL cleanup evidence
  (``functional_cleanup``), never a throughput claim.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import (
    STATUS_FAILED,
    STATUS_MEASURED,
)
from .metrics import (
    run as measure_run,
)

#: Versioned identity of the Direct Write benchmark contract (DBFB-PERF-002).
CONTRACT_DIRECT_WRITE = "dbfbridge-direct-write-v1"
CONTRACT_DIRECT_WRITE_VERSION = 1

SCENARIO_W1 = "direct_write_190k_flat"
SCENARIO_W2 = "direct_read_transform_write_190k"
SCENARIO_W3 = "direct_write_1m_flat"
SCENARIO_W4 = "direct_write_character_heavy"
SCENARIO_W5 = "direct_write_memo_heavy"
SCENARIO_W6 = "direct_write_deleted_include"
SCENARIO_W7 = "direct_write_cp1250"
SCENARIO_W8 = "direct_write_cp852"
SCENARIO_W9 = "direct_write_mazovia"
SCENARIO_W10 = "direct_write_varchar_nullflags"
SCENARIO_W11 = "overwrite_transaction_staging_cost"
SCENARIO_W12 = "cancellation_cleanup_smoke"

#: All scenario identifiers of this contract (DBFB-PERF-003).
SCENARIO_IDS = (
    SCENARIO_W1,
    SCENARIO_W2,
    SCENARIO_W3,
    SCENARIO_W4,
    SCENARIO_W5,
    SCENARIO_W6,
    SCENARIO_W7,
    SCENARIO_W8,
    SCENARIO_W9,
    SCENARIO_W10,
    SCENARIO_W11,
    SCENARIO_W12,
)

#: W12 is FUNCTIONAL cleanup evidence, never throughput (DBFB-PERF-003).
SCENARIO_KINDS = {
    SCENARIO_W1: "throughput",
    SCENARIO_W2: "throughput_transform_pipeline",
    SCENARIO_W3: "throughput_bounded_memory",
    SCENARIO_W4: "throughput_character_heavy",
    SCENARIO_W5: "throughput_memo_heavy",
    SCENARIO_W6: "throughput_deleted_records",
    SCENARIO_W7: "throughput_encoding_cp1250",
    SCENARIO_W8: "throughput_encoding_cp852",
    SCENARIO_W9: "throughput_encoding_mazovia",
    SCENARIO_W10: "throughput_replay_path",
    SCENARIO_W11: "transaction_staging_cost",
    SCENARIO_W12: "functional_cleanup",
}

#: Direct Write never writes a JSONL intermediate — explicit, not inferred.
INTERMEDIATE_JSONL_BYTES = 0

#: Architecture record counts for the FULL profile (W12 is functional and
#: only needs a bounded stream ceiling; W10's 100k exceeds the bounded
#: spool memory threshold, so the full profile MUST spill to disk).
#: W2 is architecture-FIXED at 190k; W4-W9/W11 counts are deterministic
#: repository choices (documented rationale, NOT architecture requirements):
#: character/memo/deleted/encoding workloads mirror the established Phase 3
#: workload family sizes; the overwrite transaction adds full pair staging.
FULL_COUNTS = {
    SCENARIO_W1: 190_000,
    SCENARIO_W2: 190_000,
    SCENARIO_W3: 1_000_000,
    SCENARIO_W4: 100_000,
    SCENARIO_W5: 100_000,
    SCENARIO_W6: 100_000,
    SCENARIO_W7: 50_000,
    SCENARIO_W8: 50_000,
    SCENARIO_W9: 50_000,
    SCENARIO_W10: 100_000,
    SCENARIO_W11: 20_000,
    SCENARIO_W12: 1_000,
}
COUNT_RATIONALE = {
    SCENARIO_W1: "architecture-defined (190k flat)",
    SCENARIO_W2: "architecture-defined (190k pipeline)",
    SCENARIO_W3: "architecture-defined (1M bounded memory)",
    SCENARIO_W4: "repository choice: mirrors the established 190k-workload "
    "family at a practical CI scale for a pure Character workload",
    SCENARIO_W5: "repository choice: 100k text+binary memos produce a "
    "significant FPT without dominating the profile runtime",
    SCENARIO_W6: "repository choice: mirrors the deleted-include workload "
    "family; one third of the records carry delete markers",
    SCENARIO_W7: "repository choice: Polish diacritics at 50k keep the "
    "round-trip meaningful and the full profile practical",
    SCENARIO_W8: "repository choice: same rationale as W7",
    SCENARIO_W9: "repository choice: same rationale as W7 (Mazovia/PIAST)",
    SCENARIO_W10: "existing accepted F1 full count (spills to disk)",
    SCENARIO_W11: "repository choice: 20k DBF+FPT overwrite gives a "
    "measurable staged transaction cost",
    SCENARIO_W12: "functional ceiling only (cancellation before publication)",
}
#: CI-feasible smoke counts (wiring/contract validation, NOT final evidence).
SMOKE_COUNTS = {
    SCENARIO_W1: 2_000,
    SCENARIO_W2: 2_000,
    SCENARIO_W3: 5_000,
    SCENARIO_W4: 1_000,
    SCENARIO_W5: 300,
    SCENARIO_W6: 1_000,
    SCENARIO_W7: 500,
    SCENARIO_W8: 500,
    SCENARIO_W9: 500,
    SCENARIO_W10: 500,
    SCENARIO_W11: 300,
    SCENARIO_W12: 200,
}

#: Metrics every Direct Write scenario row must carry (DBFB-PERF-001).
REQUIRED_ROW_KEYS = (
    "benchmark_contract",
    "benchmark_contract_version",
    "measured_code_sha",
    "scenario",
    "scenario_kind",
    "status",
    "record_count",
    "wall_seconds",
    "cpu_seconds",
    "records_per_second",
    "output_dbf_fpt_mib_per_second",
    "rss_before_bytes",
    "peak_rss_bytes",
    "peak_rss_delta_bytes",
    "rss_after_bytes",
    "temporary_publish_bytes_written",
    "private_spool_bytes_written",
    "temporary_bytes_written",
    "temporary_bytes_left",
    "final_output_bytes",
    "intermediate_jsonl_bytes",
    "validation",
)

#: Direct Write staging-residue name conventions (benchmark-side observation).
_RESIDUE_PATTERNS = ("*.partial*", "*.publish-backup*", "*.direct-write.spool*")


def _git_sha() -> str:
    """Current HEAD SHA, or ``unknown`` outside a git checkout."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _new_run_id() -> str:
    """Stable ``run-<32 hex>`` identifier (existing artifact convention)."""
    return f"run-{secrets.token_hex(16)}"


# ---------------------------------------------------------------------------
# benchmark-side staging/spool instrumentation (no private imports)
# ---------------------------------------------------------------------------


class SpoolTracker:
    """Observe the Direct Write staging area WITHOUT production changes.

    The scenario supplies an explicit ``staging_directory`` through the
    PUBLIC ``write_table`` API; this tracker scopes itself to exactly that
    directory:

    - it intercepts ``pathlib.Path.unlink`` ONLY for names carrying the
      ``.direct-write.spool`` segment inside the staging root, stats the
      file's logical size BEFORE the real unlink, calls the original
      implementation and restores it in ``finally`` (exception-safe);
    - it never inspects anything outside the authorized scenario staging
      root and never imports private writer modules.
    """

    SPOOL_SEGMENT = ".direct-write.spool"

    def __init__(self, staging_root: Path) -> None:
        self.staging_root = staging_root.resolve()
        self.spool_bytes = 0
        self.spool_unlink_count = 0
        self.complete = True
        self.unavailable_reason: str | None = None
        self._original_unlink = None

    def _inside_staging_spool(self, path: Path) -> bool:
        if self.SPOOL_SEGMENT not in path.name:
            return False
        try:
            return path.resolve().is_relative_to(self.staging_root)
        except (OSError, ValueError):
            return False

    def __enter__(self) -> SpoolTracker:
        import pathlib

        self._original_unlink = pathlib.Path.unlink
        tracker = self

        def _tracked_unlink(path_self: Path, missing_ok: bool = False) -> None:
            if tracker._inside_staging_spool(path_self):
                try:
                    size = path_self.stat().st_size
                except OSError as exc:
                    tracker.complete = False
                    if tracker.unavailable_reason is None:
                        tracker.unavailable_reason = f"could not stat spool: {exc}"
                else:
                    tracker.spool_bytes += size
                    tracker.spool_unlink_count += 1
            return tracker._original_unlink(path_self, missing_ok)

        pathlib.Path.unlink = _tracked_unlink  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        import pathlib

        if self._original_unlink is not None:
            pathlib.Path.unlink = self._original_unlink  # type: ignore[assignment]
            self._original_unlink = None
        return False


def _staging_residue(root: Path) -> list[Path]:
    """Direct Write staging residue under *root* (partial/backup/spool)."""
    if not root.is_dir():
        return []
    residue: list[Path] = []
    for pattern in _RESIDUE_PATTERNS:
        residue.extend(path for path in root.rglob(pattern) if path.is_file())
    return residue


class BackupMoveTracker:
    """Observe logical backup renames inside a scenario output directory.

    Benchmark-only: wraps ``os.replace`` and records the logical size of
    every move whose DESTINATION is a ``.publish-backup`` name inside the
    scenario output directory (the overwrite transaction renames the previous
    final pair aside before publishing).  Nothing is counted as "bytes
    written" — a rename moves content the publication did not rewrite — so
    this is reported separately as ``backup_logical_bytes_moved``.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.backup_logical_bytes_moved = 0
        self.moves = 0
        self._original_replace = None

    def _is_backup_move(self, src: Path, dst: Path) -> bool:
        return (
            "publish-backup" in dst.name
            and dst.resolve().is_relative_to(self.output_dir)
            and src.resolve().is_relative_to(self.output_dir)
        )

    def __enter__(self) -> BackupMoveTracker:
        import os

        self._original_replace = os.replace

        def _tracked_replace(src, dst, *args, **kwargs):
            try:
                if self._is_backup_move(Path(src), Path(dst)):
                    self.backup_logical_bytes_moved += Path(src).stat().st_size
                    self.moves += 1
            except (OSError, ValueError):
                pass
            return self._original_replace(src, dst, *args, **kwargs)

        os.replace = _tracked_replace  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        import os

        if self._original_replace is not None:
            os.replace = self._original_replace  # type: ignore[assignment]
            self._original_replace = None
        return False


# ---------------------------------------------------------------------------
# deterministic record streams (lazy, one-shot, never materialized)
# ---------------------------------------------------------------------------


def flat_records(count: int):
    """Lazy one-shot generator for the flat scenarios (W1/W3).

    A generator function: the full input is NEVER materialized — no
    ``list(records)``/``tuple(records)``/comprehension exists for it.
    """
    for index in range(count):
        yield {
            "CODE": f"K{index:07d}",
            "AMOUNT": round((index % 10_000) / 100, 2),
            "WHEN": date(2024, 1, 1 + index % 28),
            "FLAG": index % 3 == 0,
        }


def _build_schema(
    fields: tuple,
    *,
    encoding: str = "cp1250",
    language_driver: int = 0xC8,
    has_memo: bool = False,
) -> Any:
    """Parameterized Direct Write schema builder (public model construction).

    ``has_memo`` only sets the TABLE-level memo companion facts; the FIELD
    metadata must already be truthful (each memo field constructed with
    ``is_memo=True`` and the correct ``is_binary`` — DBFB-SCHEMA-002).
    """
    from dbfbridge import TableSchema

    return TableSchema(
        path=Path("memory:direct-write-benchmark"),
        record_count=0,
        header_length=32 + 32 * len(fields) + 1,
        record_length=sum(item.length for item in fields) + 1,
        language_driver=language_driver,
        encoding=encoding,
        has_memo=has_memo,
        has_memo_flag=has_memo,
        has_structural_cdx=False,
        is_database_container=False,
        dbc_bound=False,
        dbc_backlink_path=None,
        table_flags=0x02 if has_memo else 0,
        fields=fields,
        warnings=(),
        dbversion_byte=0x30,
        dbversion_name="Visual FoxPro 6+",
        last_update=None,
        incomplete_transaction=False,
        encryption_flag=False,
        memo_companion_format="FoxPro FPF",
        memo_companion_present=has_memo,
        memo_companion_path=None,
        memo_companion_size_bytes=None,
        memo_block_size=64,
        memo_next_free_block=None,
        companion_cdx_present=False,
        companion_cdx_path=None,
    )


def _field_info(
    ordinal: int, name: str, dbf_type: str, length: int, *,
    address: int, decimals: int = 0,
    is_memo: bool = False, is_binary: bool = False,
):
    """Truthful FieldInfo construction (DBFB-SCHEMA-002).

    ``is_memo``/``is_binary`` describe the FIELD type — they are never
    derived from the table-level ``has_memo`` flag.
    """
    from dbfbridge import FieldInfo

    return FieldInfo(
        ordinal=ordinal, name=name, dbf_type=dbf_type, length=length,
        decimal_count=decimals, address=address, flags=0, index_field_flag=0,
        autoincrement_next_value=0, autoincrement_step=1, is_memo=is_memo,
        is_binary=is_binary, supported=True, dbversion_byte=0x30,
    )


def _flat_schema(*, encoding: str = "cp1250", language_driver: int = 0xC8):
    """The flat schema (format-consistent with the Direct Write contract tests)."""
    fields = (
        _field_info(1, "CODE", "C", 10, address=0),
        _field_info(2, "AMOUNT", "N", 10, address=10, decimals=2),
        _field_info(3, "WHEN", "D", 8, address=20),
        _field_info(4, "FLAG", "L", 1, address=28),
    )
    return _build_schema(fields, encoding=encoding, language_driver=language_driver)


def _character_heavy_schema():
    """Multiple Character fields with varied payload capacity (W4)."""
    fields = (
        _field_info(1, "CODE", "C", 10, address=0),
        _field_info(2, "NAME", "C", 30, address=10),
        _field_info(3, "SURNAME", "C", 25, address=40),
        _field_info(4, "STREET", "C", 40, address=65),
        _field_info(5, "CITY", "C", 20, address=105),
        _field_info(6, "NOTE", "C", 50, address=125),
    )
    return _build_schema(fields)


def _memo_heavy_schema():
    """Text (M) + binary (G) memo schema — truthful per-field metadata (W5/W11)."""
    fields = (
        _field_info(1, "CODE", "C", 10, address=0),
        _field_info(2, "NOTE", "M", 4, address=10, is_memo=True, is_binary=False),
        _field_info(3, "PICTURE", "G", 4, address=14, is_memo=True, is_binary=True),
    )
    return _build_schema(fields, has_memo=True)


def _deleted_schema():
    """Flat schema for the deleted-records scenario (W6)."""
    fields = (
        _field_info(1, "CODE", "C", 10, address=0),
        _field_info(2, "AMOUNT", "N", 10, address=10, decimals=2),
    )
    return _build_schema(fields)


def _encoding_schema(encoding: str, language_driver: int):
    """Polish-text schema for the encoding round-trip scenarios (W7/W8/W9)."""
    fields = (
        _field_info(1, "CODE", "C", 12, address=0),
        _field_info(2, "NAZWA", "C", 30, address=12),
        _field_info(3, "MIASTO", "C", 20, address=42),
    )
    return _build_schema(fields, encoding=encoding, language_driver=language_driver)


def _varchar_schema(output_dir: Path):
    """An authentic VFP 0x32 schema with Varchar/_NullFlags (public API).

    A small fixture is built with the repository's authentic low-level VFP
    builder and read back through ``read_schema``; its typed schema then
    drives a generator-fed write that exercises the private bounded
    replay/spool path (DBFB-STREAM-001..005).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import vfp_fixture_factory as factory

    from dbfbridge import read_schema

    fixture = output_dir / "varchar-schema-fixture.dbf"
    factory.build_vfp32_table(
        fixture,
        columns=[
            {"name": "CODE", "type": "C", "width": 10},
            {"name": "TXT", "type": "V", "width": 24, "nullable": True},
            {"name": "NOTE", "type": "C", "width": 30, "nullable": True},
        ],
        rows=[{"CODE": "seed", "TXT": "seed-value", "NOTE": "seed-note"}],
    )
    return read_schema(fixture)


def varchar_records(count: int, schema):
    """Lazy generator for the Varchar/_NullFlags replay scenario (W10).

    Deterministic value mix: varlength payloads of varying lengths, explicit
    NULLs and significant trailing spaces — exactly the value classes the
    replay path must carry.  The ``_NullFlags`` system column is
    writer-managed and never part of the record stream.
    """
    varchar_name = next(field.name for field in schema.fields if field.dbf_type == "V")
    for index in range(count):
        row: dict[str, Any] = {"CODE": f"V{index:07d}"}
        if index % 10 == 0:
            pass  # NULL Varchar (field omitted -> explicit NULL)
        elif index % 10 in {1, 2, 3}:
            row[varchar_name] = f"v{index % 1000:04d}"
        elif index % 10 in {4, 5, 6}:
            row[varchar_name] = f"v{index % 1000:04d}-padded"
        else:
            row[varchar_name] = f"v{index % 1000:04d}-trailing  "
        if index % 5 == 0:
            row["NOTE"] = None  # explicit NULL Character
        else:
            row["NOTE"] = f"note {index % 500:04d}"
        yield row


def character_heavy_records(count: int):
    """Lazy generator for the Character-heavy scenario (W4)."""
    for index in range(count):
        yield {
            "CODE": f"C{index:07d}",
            "NAME": f"Jan Maria {index % 1000:04d} z Zamościa",
            "SURNAME": f"Kowalski-{index % 97:02d}-Nowak",
            "STREET": f"ul. {(index % 41) * 7 + 1} Kwiatowa, lok. {index % 13}",
            "CITY": f"Miasteczko {(index % 311):03d}",
            "NOTE": f"notatka {index % 997:04d}: "
            + "ąęłóń śźż" * (1 + index % 4),
        }


def memo_heavy_records(count: int, *, generation: str = "NEW", code_prefix: str = "N"):
    """Lazy generator for the memo-heavy scenarios (W5/W11): text + binary memos.

    ``generation`` is a deterministic generation marker that changes ALL three
    value families (DBF field, text memo, binary memo) so an overwrite
    transaction genuinely replaces a DIFFERENT DBF+FPT generation (F2-BLK-02):

    - ``CODE`` uses the generation-specific prefix;
    - the text memo carries the generation marker;
    - the binary memo embeds a deterministic generation tag byte.
    """
    from .fixtures import MEMO_TEXT

    for index in range(count):
        binary = bytes(
            (index * 31 + offset) % 256 for offset in range(8 + index % 200)
        )
        if generation == "OLD":
            binary = b"\xA0" + binary  # deterministic OLD-generation tag byte
            note = f"[OLD {index % 5000:04d}] {MEMO_TEXT}"
        else:
            binary = b"\x5A" + binary  # deterministic NEW-generation tag byte
            note = f"[NEW {index % 5000:04d}] {MEMO_TEXT}"
        yield {
            "CODE": f"{code_prefix}{index:07d}",
            "NOTE": note,
            "PICTURE": binary,
        }


def deleted_records(count: int):
    """Lazy generator for the deleted-records scenario (W6).

    Every third record (index % 3 == 2) carries the public ``__deleted__``
    marker; physical ordering and the exact deleted count stay known.
    """
    for index in range(count):
        record = {
            "CODE": f"D{index:07d}",
            "AMOUNT": round((index % 5_000) / 100, 2),
        }
        if index % 3 == 2:
            record["__deleted__"] = True
        yield record


def polish_records(count: int, schema):
    """Lazy generator with real Polish diacritics (W7/W8/W9)."""
    from .fixtures import POLISH_TEXT

    names = [field.name for field in schema.fields if field.dbf_type == "C"]
    code_name, nazwa_name, miasto_name = names[0], names[1], names[2]
    for index in range(count):
        yield {
            code_name: f"{index % 100_000:05d}",
            nazwa_name: f"{POLISH_TEXT[index % len(POLISH_TEXT)]} {index % 10_000:04d}",
            miasto_name: POLISH_TEXT[(index * 3) % len(POLISH_TEXT)],
        }


# ---------------------------------------------------------------------------
# public Direct Read validation — BOUNDED (O(page), never O(total))
# ---------------------------------------------------------------------------


def _validate_flat_output(
    destination: Path, count: int, *, transformed: bool = False
) -> dict[str, Any]:
    """Exact bounded page-by-page validation of a flat/transformed write.

    For EVERY output record the deterministic expected values (CODE, AMOUNT,
    WHEN, FLAG) are recomputed from the generator contract and compared —
    the validation memory complexity is O(page size), never O(total records).
    """
    from dbfbridge import read_records

    total = 0
    first_code: str | None = None
    last_code: str | None = None
    transform_mismatches = 0
    offset = 0
    while True:
        page = read_records(destination, offset=offset, limit=50_000, memo="skip")
        for record in page.records:
            if total == 0:
                first_code = record.values.get("CODE")
            last_code = record.values.get("CODE")
            index = total
            values = record.values
            if (
                values.get("CODE") != f"{'T' if transformed else 'K'}{index:07d}"
                or values.get("AMOUNT")
                != round((index % 10_000) / 100 + (1.5 if transformed else 0), 2)
                or values.get("WHEN") != date(2024, 1, 1 + index % 28)
                or values.get("FLAG") != (index % 3 == 0)
            ):
                transform_mismatches += 1
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count, (total, count)
    result = {
        "record_count": total,
        "first_code": first_code,
        "last_code": last_code,
        "transform_mismatches": transform_mismatches,
        "canonical_values_verified": transform_mismatches == 0,
        "bounded_validation": True,
    }
    if transformed:
        result["transform_verified"] = transform_mismatches == 0
    return result


def _validate_character_output(destination: Path, count: int) -> dict[str, Any]:
    """Exact bounded Character-heavy validation (W4).

    Every output record's six Character fields are compared against the
    deterministic generator values; only counters and first/last facts are
    retained — O(page size).
    """
    from dbfbridge import read_records

    total = 0
    first_name: str | None = None
    last_city: str | None = None
    character_value_mismatches = 0
    offset = 0
    while True:
        page = read_records(destination, offset=offset, limit=50_000, memo="skip")
        for record in page.records:
            values = record.values
            index = total
            expected_note = (
                f"notatka {index % 997:04d}: " + "ąęłóń śźż" * (1 + index % 4)
            )
            if (
                values.get("CODE") != f"C{index:07d}"
                or values.get("NAME") != f"Jan Maria {index % 1000:04d} z Zamościa"[:30]
                or values.get("SURNAME") != f"Kowalski-{index % 97:02d}-Nowak"[:25]
                or values.get("STREET")
                != f"ul. {(index % 41) * 7 + 1} Kwiatowa, lok. {index % 13}"[:40]
                or values.get("CITY") != f"Miasteczko {(index % 311):03d}"[:20]
                or values.get("NOTE") != expected_note[:50]
            ):
                character_value_mismatches += 1
            if total == 0:
                first_name = values.get("NAME")
            last_city = values.get("CITY")
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count
    return {
        "record_count": total,
        "first_name": first_name,
        "last_city": last_city,
        "character_value_mismatches": character_value_mismatches,
        "canonical_values_verified": character_value_mismatches == 0,
        "bounded_validation": True,
    }


def _validate_memo_output(
    destination: Path, count: int, *, generation: str = "NEW", code_prefix: str = "N"
) -> dict[str, Any]:
    """Exact bounded memo validation via public read_records(memo="inline").

    For EVERY record the deterministic CODE, text memo and binary memo values
    are recomputed from the generator contract and compared; memo TYPE
    semantics (NOTE str, PICTURE bytes) are verified per record.  Only
    counters and small first/last facts are retained — O(page size); full
    memo payloads never enter the artifact.
    """
    from dbfbridge import read_records

    total = 0
    code_mismatches = 0
    text_memo_mismatches = 0
    binary_memo_mismatches = 0
    memo_type_mismatches = 0
    first_code: str | None = None
    last_code: str | None = None
    first_note: str | None = None
    last_note: str | None = None
    offset = 0
    while True:
        page = read_records(destination, offset=offset, limit=50_000, memo="inline")
        for record in page.records:
            values = record.values
            index = total
            code = values.get("CODE")
            note = values.get("NOTE")
            picture = values.get("PICTURE")
            if code != f"{code_prefix}{index:07d}":
                code_mismatches += 1
            if total == 0:
                first_code = code
                first_note = note
            last_code = code
            last_note = note
            if not isinstance(note, str) or not isinstance(picture, bytes):
                memo_type_mismatches += 1
                continue
            expected_note = f"[{generation} {index % 5000:04d}] " + str(
                __import__("benchmarks.fixtures", fromlist=["MEMO_TEXT"]).MEMO_TEXT
            )
            expected_binary = bytes(
                (index * 31 + offset) % 256 for offset in range(8 + index % 200)
            )
            expected_binary = (b"\xA0" if generation == "OLD" else b"\x5A") + expected_binary
            if note != expected_note:
                text_memo_mismatches += 1
            if picture != expected_binary:
                binary_memo_mismatches += 1
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count
    return {
        "record_count": total,
        "first_code": first_code,
        "last_code": last_code,
        "first_note_prefix": (first_note or "")[:6],
        "last_note_prefix": (last_note or "")[:6],
        "code_mismatches": code_mismatches,
        "text_memo_mismatches": text_memo_mismatches,
        "binary_memo_mismatches": binary_memo_mismatches,
        "memo_type_mismatches": memo_type_mismatches,
        "memo_semantics_verified": (
            code_mismatches == 0
            and text_memo_mismatches == 0
            and binary_memo_mismatches == 0
            and memo_type_mismatches == 0
        ),
        "generation": generation,
        "fpt_published": True,
        "bounded_validation": True,
    }


def _validate_deleted_output(
    destination: Path, count: int, expected_deleted: int
) -> dict[str, Any]:
    """Bounded deleted-records validation: markers and ordering (W6)."""
    from dbfbridge import read_records

    total = 0
    deleted = 0
    first_code: str | None = None
    last_code: str | None = None
    ordering_matches = True
    mismatches: list[Any] = []
    offset = 0
    while True:
        page = read_records(
            destination, offset=offset, limit=50_000, memo="skip", include_deleted=True
        )
        for record in page.records:
            if total == 0:
                first_code = record.values.get("CODE")
            last_code = record.values.get("CODE")
            if record.deleted != (total % 3 == 2):
                ordering_matches = False
                if len(mismatches) < 20:
                    mismatches.append(
                        (total, record.values.get("CODE"), record.deleted)
                    )
            if record.deleted:
                deleted += 1
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count
    return {
        "record_count": total,
        "first_code": first_code,
        "last_code": last_code,
        "deleted_count": deleted,
        "expected_deleted_count": expected_deleted,
        "ordering_matches_input": ordering_matches,
        "deleted_semantics_verified": deleted == expected_deleted and ordering_matches,
        "bounded_validation": True,
    }

def _validate_encoding_output(
    destination: Path, count: int, encoding: str, language_driver: int
) -> dict[str, Any]:
    """Exact bounded encoding round-trip validation (W7/W8/W9).

    For EVERY record all three Character fields (CODE, NAZWA, MIASTO) are
    compared against the deterministic Polish expectations; the intended
    language driver is verified via PUBLIC ``read_schema`` read-back.
    """
    from benchmarks.fixtures import POLISH_TEXT
    from dbfbridge import read_records, read_schema

    schema = read_schema(destination)
    schema_driver_verified = schema.language_driver == language_driver
    total = 0
    first_nazwa: str | None = None
    last_nazwa: str | None = None
    canonical_mismatches = 0
    offset = 0
    while True:
        page = read_records(destination, offset=offset, limit=50_000, memo="skip")
        for record in page.records:
            values = record.values
            index = total
            expected = {
                "CODE": f"{index % 100_000:05d}",
                "NAZWA": (
                    f"{POLISH_TEXT[index % len(POLISH_TEXT)]} {index % 10_000:04d}"
                ),
                "MIASTO": POLISH_TEXT[(index * 3) % len(POLISH_TEXT)],
            }
            if (
                values.get("CODE") != expected["CODE"]
                or values.get("NAZWA") != expected["NAZWA"]
                or values.get("MIASTO") != expected["MIASTO"]
            ):
                canonical_mismatches += 1
            if total == 0:
                first_nazwa = values.get("NAZWA")
            last_nazwa = values.get("NAZWA")
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count
    return {
        "record_count": total,
        "first_nazwa": first_nazwa,
        "last_nazwa": last_nazwa,
        "canonical_mismatches": canonical_mismatches,
        "encoding": encoding,
        "language_driver": f"0x{language_driver:02X}",
        "schema_driver_verified": schema_driver_verified,
        "schema_resolved_encoding": schema.encoding,
        "encoding_round_trip_verified": canonical_mismatches == 0
        and schema_driver_verified,
        "bounded_validation": True,
    }


def _validate_varchar_output(destination: Path, count: int) -> dict[str, Any]:
    """Bounded page-by-page validation incl. NULL/Varchar semantics (W10)."""
    from dbfbridge import read_records

    total = 0
    null_varchars = 0
    null_notes = 0
    first_code: str | None = None
    last_code: str | None = None
    offset = 0
    while True:
        page = read_records(destination, offset=offset, limit=50_000, memo="inline")
        for record in page.records:
            if total == 0:
                first_code = record.values.get("CODE")
            last_code = record.values.get("CODE")
            if record.values.get("TXT") is None:
                null_varchars += 1
            if record.values.get("NOTE") is None:
                null_notes += 1
            total += 1
        if page.exhausted or page.next_offset is None:
            break
        offset = page.next_offset
    assert total == count
    assert null_varchars == count // 10 + (1 if count % 10 else 0)
    assert null_notes == count // 5 + (1 if count % 5 else 0)
    return {
        "record_count": total,
        "null_varchar_count": null_varchars,
        "null_note_count": null_notes,
        "first_code": first_code,
        "last_code": last_code,
        "varchar_null_semantics_verified": True,
        "bounded_validation": True,
    }


# ---------------------------------------------------------------------------
# scenario runners (public API only)
# ---------------------------------------------------------------------------


def _run_row(
    scenario_id: str,
    measured: dict[str, Any],
    *,
    record_count: int | None,
    validation: dict[str, Any],
    dbf_bytes: int,
    fpt_bytes: int,
    spool: SpoolTracker,
    residue_paths: list[Path],
    backup_logical_bytes_moved: int | None = None,
) -> dict[str, Any]:
    """Assemble one artifact row with the DISTINCT temporary byte model.

    ``temporary_publish_bytes_written`` (atomic ``.partial`` publishes) and
    ``private_spool_bytes_written`` (observed staging-area spool unlinks) are
    separate measurements; ``temporary_bytes_written`` is their sum when the
    instrumentation is complete, ``null`` + reason otherwise.
    """
    wall = measured.get("wall_seconds") or 0.0
    total_output = dbf_bytes + fpt_bytes
    publish_bytes = measured.get("temporary_bytes_written")
    spool_bytes = spool.spool_bytes
    if publish_bytes is None or not spool.complete:
        temporary_written: int | None = None
        temporary_reason = (
            spool.unavailable_reason
            or "publish-byte instrumentation incomplete (NOT_AVAILABLE)"
        )
    else:
        temporary_written = publish_bytes + spool_bytes
        temporary_reason = None
    residue_bytes = sum(path.stat().st_size for path in residue_paths)
    row: dict[str, Any] = {
        "benchmark_contract": CONTRACT_DIRECT_WRITE,
        "benchmark_contract_version": CONTRACT_DIRECT_WRITE_VERSION,
        "measured_code_sha": _git_sha(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "scenario": scenario_id,
        "scenario_kind": SCENARIO_KINDS[scenario_id],
        "status": measured.get("status"),
        "record_count": record_count,
        "count_rationale": COUNT_RATIONALE.get(scenario_id),
        "wall_seconds": measured.get("wall_seconds"),
        "cpu_seconds": measured.get("cpu_seconds"),
        "records_per_second": measured.get("records_per_second"),
        "source_mib_per_second": measured.get("source_mib_per_second"),
        "output_dbf_fpt_mib_per_second": (
            round(total_output / (1024 * 1024) / wall, 4)
            if wall > 0 and total_output > 0
            else None
        ),
        "rss_before_bytes": measured.get("rss_before_bytes"),
        "peak_rss_bytes": measured.get("peak_rss_bytes"),
        "peak_rss_delta_bytes": measured.get("peak_rss_delta_bytes"),
        "rss_after_bytes": measured.get("rss_after_bytes"),
        "temporary_publish_bytes_written": publish_bytes,
        "private_spool_bytes_written": spool_bytes,
        "temporary_bytes_written": temporary_written,
        "temporary_bytes_left": residue_bytes,
        "temporary_residue_paths": [path.name for path in residue_paths],
        "backup_logical_bytes_moved": backup_logical_bytes_moved,
        "final_output_bytes": total_output,
        "dbf_bytes": dbf_bytes,
        "fpt_bytes": fpt_bytes,
        "intermediate_jsonl_bytes": INTERMEDIATE_JSONL_BYTES,
        "validation": validation,
    }
    if temporary_reason is not None:
        row["temporary_bytes_written_reason"] = temporary_reason
    if measured.get("status") == STATUS_FAILED:
        row["error"] = measured.get("error")
    return row


def _scenario_w1(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W1 direct_write_190k_flat`` — the plain flat Direct Write path."""
    from dbfbridge import write_table

    destination = output_dir / "w1_flat.dbf"
    schema = _flat_schema()
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=flat_records(count),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_flat_output(destination, count)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    fpt_path = destination.with_suffix(".fpt")
    return _run_row(
        SCENARIO_W1,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=fpt_path.stat().st_size if fpt_path.exists() else 0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w3(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W3 direct_write_1m_flat`` — DBFB-PERF-004 bounded-input evidence."""
    from dbfbridge import write_table

    destination = output_dir / "w3_flat.dbf"
    schema = _flat_schema()
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=flat_records(count),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_flat_output(destination, count)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    fpt_path = destination.with_suffix(".fpt")
    return _run_row(
        SCENARIO_W3,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=fpt_path.stat().st_size if fpt_path.exists() else 0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w10(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W10 direct_write_varchar_nullflags`` — the bounded replay/spool path.

    The full profile's 100,000 records exceed the bounded spool's memory
    threshold, so the path MUST spill to the private staging spool; the
    spool bytes are OBSERVED at unlink time inside the scenario-local
    staging directory (never derived from the DBF size).
    """
    from dbfbridge import write_table

    schema = _varchar_schema(output_dir)
    destination = output_dir / "w10_varchar.dbf"
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=varchar_records(count, schema),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_varchar_output(destination, count)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    fpt_path = destination.with_suffix(".fpt")
    return _run_row(
        SCENARIO_W10,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=fpt_path.stat().st_size if fpt_path.exists() else 0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w2(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W2 direct_read_transform_write_190k`` — DBFB-COST-001 pipeline.

    The source dataset is prepared OUTSIDE the measured window; the measured
    operation is the PUBLIC lazy pipeline ``iter_records -> transform ->
    write_table`` (no JSONL, no schema.json, no migration report).  The
    source DBF must remain byte-for-byte unchanged.
    """
    import hashlib

    from dbfbridge import iter_records, write_table

    schema = _flat_schema()
    source = output_dir / "w2_source.dbf"
    write_table(source, schema=schema, records=flat_records(count))
    source_sha_before = hashlib.sha256(source.read_bytes()).hexdigest()
    source_bytes = source.stat().st_size
    source_mtime_before = source.stat().st_mtime_ns
    destination = output_dir / "w2_transformed.dbf"
    spool = SpoolTracker(staging)

    def transform_records():
        for position, record in enumerate(iter_records(source, memo="skip")):
            values = dict(record.values)
            # deterministic non-identity transform of ordinary supported fields
            values["CODE"] = "T" + str(values.get("CODE", ""))[1:]
            values["AMOUNT"] = round((values.get("AMOUNT") or 0) + 1.5, 2)
            assert position >= 0  # single pass, lazily consumed
            yield values

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=transform_records(),
                staging_directory=staging,
            )

    measured = measure_run(
        run,
        input_bytes=source_bytes,
        input_records=count,
        output_dir=output_dir,
    )
    validation = (
        _validate_flat_output(destination, count, transformed=True)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    source_sha_after = hashlib.sha256(source.read_bytes()).hexdigest()
    source_stat_after = source.stat()
    validation["source_sha256_before"] = source_sha_before
    validation["source_sha256_after"] = source_sha_after
    validation["source_size_before"] = source_bytes
    validation["source_size_after"] = source_stat_after.st_size
    validation["source_mtime_ns_before"] = source_mtime_before
    validation["source_mtime_ns_after"] = source_stat_after.st_mtime_ns
    validation["source_unchanged"] = (
        source_sha_before == source_sha_after
        and source_bytes == source_stat_after.st_size
        and source_mtime_before == source_stat_after.st_mtime_ns
    )
    validation["source_fpt_applicable"] = False  # W2 flat source has no FPT
    fpt_path = destination.with_suffix(".fpt")
    return _run_row(
        SCENARIO_W2,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=fpt_path.stat().st_size if fpt_path.exists() else 0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w4(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W4 direct_write_character_heavy`` — Character payload density."""
    from dbfbridge import write_table

    destination = output_dir / "w4_character.dbf"
    schema = _character_heavy_schema()
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=character_heavy_records(count),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_character_output(destination, count)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    return _run_row(
        SCENARIO_W4,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w5(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W5 direct_write_memo_heavy`` — text (M) + binary (G) memo FPT output."""
    from dbfbridge import write_table

    destination = output_dir / "w5_memo.dbf"
    schema = _memo_heavy_schema()
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=memo_heavy_records(count, generation="NEW", code_prefix="M"),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_memo_output(destination, count, generation="NEW", code_prefix="M")
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    return _run_row(
        SCENARIO_W5,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=(
            destination.with_suffix(".fpt").stat().st_size
            if destination.with_suffix(".fpt").exists()
            else 0
        ),
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w6(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W6 direct_write_deleted_include`` — deleted-marker round trip (W6)."""
    from dbfbridge import write_table

    destination = output_dir / "w6_deleted.dbf"
    schema = _deleted_schema()
    expected_deleted = count // 3  # O(1): index % 3 == 2 (F2-BLK-05)
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=deleted_records(count),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_deleted_output(destination, count, expected_deleted)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    return _run_row(
        SCENARIO_W6,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_encoding(
    output_dir: Path,
    staging: Path,
    count: int,
    *,
    scenario_id: str,
    encoding: str,
    language_driver: int,
) -> dict[str, Any]:
    """``W7/W8/W9`` — Polish-encoding round trips through the public API."""
    from dbfbridge import write_table

    destination = output_dir / f"{scenario_id}.dbf"
    schema = _encoding_schema(encoding, language_driver)
    spool = SpoolTracker(staging)

    def run() -> None:
        with spool:
            write_table(
                destination,
                schema=schema,
                records=polish_records(count, schema),
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_encoding_output(destination, count, encoding, language_driver)
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    return _run_row(
        scenario_id,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
    )


def _scenario_w11(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W11 overwrite_transaction_staging_cost`` — measured transaction cost.

    A pre-existing DBF+FPT pair (OLD generation: CODE prefix ``O``, OLD text/
    binary memo markers) is replaced through the normal overwrite transaction
    with a DETERMINISTICALLY DIFFERENT NEW generation (CODE prefix ``N``, NEW
    memo markers).  Transaction semantics are NOT weakened; failure semantics
    remain the authority of the existing failure-injection tests.
    """
    import hashlib

    from dbfbridge import write_table

    destination = output_dir / "w11_pair.dbf"
    schema = _memo_heavy_schema()
    # Pre-existing OLD-generation pair OUTSIDE the measured window.
    write_table(
        destination,
        schema=schema,
        records=memo_heavy_records(count, generation="OLD", code_prefix="O"),
        staging_directory=staging,
    )
    preexisting_dbf = destination.stat().st_size
    preexisting_fpt = destination.with_suffix(".fpt").stat().st_size
    preexisting_dbf_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    preexisting_fpt_sha = hashlib.sha256(
        destination.with_suffix(".fpt").read_bytes()
    ).hexdigest()
    spool = SpoolTracker(staging)
    moves = BackupMoveTracker(output_dir)

    def run() -> None:
        with spool, moves:
            write_table(
                destination,
                schema=schema,
                records=memo_heavy_records(count, generation="NEW", code_prefix="N"),
                overwrite=True,
                staging_directory=staging,
            )

    measured = measure_run(
        run, input_bytes=None, input_records=count, output_dir=output_dir
    )
    validation = (
        _validate_memo_output(destination, count, generation="NEW", code_prefix="N")
        if measured.get("status") == STATUS_MEASURED
        else {"verified": False}
    )
    fpt_path = destination.with_suffix(".fpt")
    final_dbf_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    final_fpt_sha = hashlib.sha256(fpt_path.read_bytes()).hexdigest()
    validation["final_dbf_sha256"] = final_dbf_sha
    validation["final_fpt_sha256"] = final_fpt_sha
    validation["preexisting_dbf_sha256"] = preexisting_dbf_sha
    validation["preexisting_fpt_sha256"] = preexisting_fpt_sha
    validation["old_new_dbf_differ"] = preexisting_dbf_sha != final_dbf_sha
    validation["old_new_fpt_differ"] = preexisting_fpt_sha != final_fpt_sha
    validation["preexisting_dbf_bytes"] = preexisting_dbf
    validation["preexisting_fpt_bytes"] = preexisting_fpt
    validation["final_dbf_bytes"] = (
        destination.stat().st_size if destination.exists() else 0
    )
    validation["final_fpt_bytes"] = (
        fpt_path.stat().st_size if fpt_path.exists() else 0
    )
    # F2-BLK-06: NEW-generation proof requires exact CODE evidence, not
    # merely a hash difference.
    expected_first_code = f"N{0:07d}"
    expected_last_code = f"N{(count - 1):07d}"
    validation["expected_first_code"] = expected_first_code
    validation["expected_last_code"] = expected_last_code
    validation["new_generation_verified"] = (
        validation.get("memo_semantics_verified") is True
        and validation.get("generation") == "NEW"
        and validation.get("code_mismatches") == 0
        and validation.get("first_code") == expected_first_code
        and validation.get("last_code") == expected_last_code
        and validation["old_new_dbf_differ"]
        and validation["old_new_fpt_differ"]
    )
    row = _run_row(
        SCENARIO_W11,
        measured,
        record_count=count,
        validation=validation,
        dbf_bytes=destination.stat().st_size if destination.exists() else 0,
        fpt_bytes=fpt_path.stat().st_size if fpt_path.exists() else 0,
        spool=spool,
        residue_paths=_staging_residue(output_dir),
        backup_logical_bytes_moved=(
            moves.backup_logical_bytes_moved if moves.moves else 0
        ),
    )
    row["preexisting_final_bytes"] = preexisting_dbf + preexisting_fpt
    row["preexisting_dbf_bytes"] = preexisting_dbf
    row["preexisting_fpt_bytes"] = preexisting_fpt
    row["final_dbf_bytes"] = row["dbf_bytes"]
    row["final_fpt_bytes"] = row["fpt_bytes"]
    row["backup_moves"] = moves.moves
    return row


def _scenario_w12(output_dir: Path, staging: Path, count: int) -> dict[str, Any]:
    """``W12 cancellation_cleanup_smoke`` — FUNCTIONAL, never throughput.

    Deterministic cancellation after a bounded number of records, before
    publication, on the FLAT path (no spool is applicable — stated
    truthfully).  The typed ``WRITE_CANCELLED`` family is expected; nothing
    may be published and no residue may remain.
    """
    from dbfbridge import WriteCancelledError, write_table

    destination = output_dir / "w12_cancelled.dbf"
    schema = _flat_schema()
    state: dict[str, Any] = {
        "consumed": 0,
        "cancelled": False,
        "write_cancelled_typed": False,
        "error_code": None,
    }
    spool = SpoolTracker(staging)

    def cancel_after_ten() -> bool:
        return state["consumed"] >= 10

    def records():
        for index, record in enumerate(flat_records(count)):
            state["consumed"] = index
            yield record

    def run() -> None:
        with spool:
            try:
                write_table(
                    destination,
                    schema=schema,
                    records=records(),
                    overwrite=False,
                    staging_directory=staging,
                    cancel_check=cancel_after_ten,
                )
                state["cancelled"] = False
            except WriteCancelledError as exc:
                state["cancelled"] = True
                state["write_cancelled_typed"] = True
                code = exc.code
                state["error_code"] = getattr(code, "value", str(code))

    measured = measure_run(
        run, input_bytes=None, input_records=None, output_dir=output_dir
    )
    residue_paths = _staging_residue(output_dir)
    validation = {
        "cancelled": state["cancelled"],
        "write_cancelled_typed": state["write_cancelled_typed"],
        "error_code": state["error_code"],
        "records_consumed_before_cancel": state["consumed"],
        "destination_absent": not destination.exists(),
        "fpt_absent": not destination.with_suffix(".fpt").exists(),
        "no_partial_residue": not any("partial" in path.name for path in residue_paths),
        "no_backup_residue": not any("publish-backup" in path.name for path in residue_paths),
        "no_spool_residue": not any(
            SpoolTracker.SPOOL_SEGMENT in path.name for path in residue_paths
        ),
        "spool_applicable": False,  # flat path: truthfully not applicable
        "cleanup_verified": (
            state["cancelled"]
            and state["write_cancelled_typed"]
            and not destination.exists()
            and not residue_paths
        ),
    }
    row = _run_row(
        SCENARIO_W12,
        measured,
        record_count=None,
        validation=validation,
        dbf_bytes=0,
        fpt_bytes=0,
        spool=spool,
        residue_paths=residue_paths,
    )
    # W12 is functional: no throughput claim may be derived from it.
    row["records_per_second"] = None
    return row


# ---------------------------------------------------------------------------
# artifact assembly / validation
# ---------------------------------------------------------------------------


def _memory_comparison(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Measured W1-vs-W3 memory FACTS (DBFB-PERF-004).

    The benchmark is MEASUREMENT infrastructure, not architecture policy:
    it reports the measured counts, RSS baselines, peaks, deltas and their
    descriptive ratios — and NOTHING else.  No numeric pass/fail threshold
    and no automatic DBFB-PERF-004 verdict is established here; the
    independent architect determines acceptance from these facts plus the
    source-level streaming evidence.
    """
    w1 = rows[SCENARIO_W1]
    w3 = rows[SCENARIO_W3]
    facts: dict[str, Any] = {
        "smaller_point": SCENARIO_W1,
        "larger_point": SCENARIO_W3,
        "same_generator_family": True,
        "w1_record_count": w1.get("record_count"),
        "w1_rss_before_bytes": w1.get("rss_before_bytes"),
        "w1_peak_rss_bytes": w1.get("peak_rss_bytes"),
        "w1_peak_rss_delta_bytes": w1.get("peak_rss_delta_bytes"),
        "w3_record_count": w3.get("record_count"),
        "w3_rss_before_bytes": w3.get("rss_before_bytes"),
        "w3_peak_rss_bytes": w3.get("peak_rss_bytes"),
        "w3_peak_rss_delta_bytes": w3.get("peak_rss_delta_bytes"),
        # Descriptive normalization ONLY: process RSS does not directly prove
        # per-record retention semantics, so these values are labelled as
        # measured deltas divided by the input record count.
        "w1_peak_rss_delta_bytes_per_input_record": None,
        "w3_peak_rss_delta_bytes_per_input_record": None,
    }
    w1_count = w1.get("record_count") or 0
    w3_count = w3.get("record_count") or 0
    w1_delta = w1.get("peak_rss_delta_bytes")
    w3_delta = w3.get("peak_rss_delta_bytes")
    if w1_count and w3_count and isinstance(w1_delta, int) and isinstance(w3_delta, int):
        facts["record_count_ratio"] = round(w3_count / w1_count, 4)
        w1_peak = w1.get("peak_rss_bytes")
        w3_peak = w3.get("peak_rss_bytes")
        if isinstance(w1_peak, int) and w1_peak > 0 and isinstance(w3_peak, int):
            facts["peak_rss_ratio"] = round(w3_peak / w1_peak, 4)
        facts["peak_rss_delta_ratio"] = round(w3_delta / w1_delta, 4) if w1_delta > 0 else None
        facts["w1_peak_rss_delta_bytes_per_input_record"] = round(
            w1_delta / w1_count, 4
        )
        facts["w3_peak_rss_delta_bytes_per_input_record"] = round(
            w3_delta / w3_count, 4
        )
    else:
        facts["record_count_ratio"] = None
        facts["peak_rss_ratio"] = None
        facts["peak_rss_delta_ratio"] = None
    facts["assessment"] = "MEASURED_FACTS_ONLY"
    facts["note"] = (
        "The benchmark reports measured RSS facts and source-level streaming "
        "evidence.  No repository regression threshold or automatic "
        "DBFB-PERF-004 pass/fail policy is established by this first "
        "measured profile."
    )
    return facts


def validate_artifact(payload: dict[str, Any]) -> list[str]:
    """Structural validator for the Direct Write benchmark artifact.

    Hard gates ONLY (DBFB-PERF-006): contract identity, provenance, scenario
    coverage, required metric keys, explicit zero intermediate JSONL, zero
    residue for completed throughput scenarios, correct W12 functional
    semantics and public-read record-count parity.  Performance values are
    measured and reported — never gated against a threshold.
    """
    problems: list[str] = []
    if payload.get("benchmark_contract") != CONTRACT_DIRECT_WRITE:
        problems.append("unexpected benchmark_contract")
    if payload.get("benchmark_contract_version") != CONTRACT_DIRECT_WRITE_VERSION:
        problems.append("unexpected benchmark_contract_version")
    if not payload.get("measured_code_sha"):
        problems.append("missing measured_code_sha provenance")
    rows = payload.get("scenarios")
    if not isinstance(rows, list) or not rows:
        problems.append("no scenario rows")
        return problems
    seen: set[str] = set()
    for row in rows:
        scenario = row.get("scenario")
        seen.add(scenario)
        missing = [key for key in REQUIRED_ROW_KEYS if key not in row]
        if missing:
            problems.append(f"{scenario}: missing keys {missing}")
        if row.get("intermediate_jsonl_bytes") != 0:
            problems.append(f"{scenario}: intermediate_jsonl_bytes must be 0")
        if row.get("scenario_kind") != SCENARIO_KINDS.get(scenario):
            problems.append(f"{scenario}: unexpected scenario_kind")
        if row.get("status") not in {STATUS_MEASURED, STATUS_FAILED}:
            problems.append(f"{scenario}: status must be MEASURED or FAILED")
        if (
            row.get("status") == STATUS_MEASURED
            and scenario != SCENARIO_W12
            and row.get("temporary_bytes_left") != 0
        ):
            problems.append(f"{scenario}: temporary residue must be 0")
        if scenario == SCENARIO_W12:
            validation = row.get("validation") or {}
            if row.get("scenario_kind") != "functional_cleanup":
                problems.append("W12 must be functional_cleanup")
            if row.get("records_per_second") is not None:
                problems.append("W12 must not claim a throughput value")
            if not validation.get("cleanup_verified"):
                problems.append("W12 cleanup not verified")
        if (
            row.get("status") == STATUS_MEASURED
            and scenario in (SCENARIO_W1, SCENARIO_W3, SCENARIO_W10)
            and (row.get("validation") or {}).get("record_count")
            != row.get("record_count")
        ):
            problems.append(f"{scenario}: validated record count mismatch")
    missing_scenarios = set(SCENARIO_IDS) - seen
    if missing_scenarios:
        problems.append(f"missing scenarios: {sorted(missing_scenarios)}")
    return problems


def build_artifact(mode: str, counts: dict[str, int], root: Path) -> dict[str, Any]:
    """Run W1-W12 and assemble the measured artifact payload.

    The caller owns *root*'s lifetime (the CLI wraps it in a
    ``TemporaryDirectory`` so scenario artifacts are transient while the
    JSON/Markdown evidence is written to ``--out`` separately).
    """
    for sub in (
        "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9", "w10", "w11", "w12",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    rows = [
        _scenario_w1(root / "w1", root / "w1" / "staging", counts[SCENARIO_W1]),
        _scenario_w2(root / "w2", root / "w2" / "staging", counts[SCENARIO_W2]),
        _scenario_w3(root / "w3", root / "w3" / "staging", counts[SCENARIO_W3]),
        _scenario_w4(root / "w4", root / "w4" / "staging", counts[SCENARIO_W4]),
        _scenario_w5(root / "w5", root / "w5" / "staging", counts[SCENARIO_W5]),
        _scenario_w6(root / "w6", root / "w6" / "staging", counts[SCENARIO_W6]),
        _scenario_encoding(
            root / "w7", root / "w7" / "staging", counts[SCENARIO_W7],
            scenario_id=SCENARIO_W7, encoding="cp1250", language_driver=0xC8,
        ),
        _scenario_encoding(
            root / "w8", root / "w8" / "staging", counts[SCENARIO_W8],
            scenario_id=SCENARIO_W8, encoding="cp852", language_driver=0x64,
        ),
        _scenario_encoding(
            root / "w9", root / "w9" / "staging", counts[SCENARIO_W9],
            scenario_id=SCENARIO_W9, encoding="mazovia", language_driver=0x69,
        ),
        _scenario_w10(root / "w10", root / "w10" / "staging", counts[SCENARIO_W10]),
        _scenario_w11(root / "w11", root / "w11" / "staging", counts[SCENARIO_W11]),
        _scenario_w12(root / "w12", root / "w12" / "staging", counts[SCENARIO_W12]),
    ]
    by_scenario = {row["scenario"]: row for row in rows}
    return {
        "benchmark_contract": CONTRACT_DIRECT_WRITE,
        "benchmark_contract_version": CONTRACT_DIRECT_WRITE_VERSION,
        "measured_code_sha": _git_sha(),
        "run_id": _new_run_id(),
        "mode": mode,
        "git_sha": _git_sha(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": dict(counts),
        "scenarios": rows,
        "w1_w3_comparison": _memory_comparison(by_scenario),
    }


def markdown_summary(payload: dict[str, Any]) -> str:
    """Human-readable summary rendered FROM the same payload (single source)."""
    lines = [
        "# dbfbridge Direct Write measured profile (dbfbridge-direct-write-v1)",
        "",
        f"Mode: `{payload['mode']}` · measured at: `{payload['measured_code_sha']}` · "
        f"Python: `{payload['python_version']}` · platform: `{payload['platform']}` "
        f"· run: `{payload['run_id']}`",
        "",
        "| scenario | kind | status | records | wall (s) | rec/s |"
        " peak Δ RSS (MiB) | publish temp (B) | spool (B) | temp total (B) |"
        " residue (B) | JSONL (B) | final out (KiB) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["scenarios"]:
        delta = row.get("peak_rss_delta_bytes")
        delta_mib = (
            f"{delta / 1048576:.1f}"
            if isinstance(delta, int)
            else ("NOT_AVAILABLE" if row.get("status") == "MEASURED" else "n/a")
        )
        rate = row.get("records_per_second")
        rate_text = str(round(rate)) if isinstance(rate, (int, float)) else "n/a"
        final_kib = f"{(row.get('final_output_bytes') or 0) / 1024:.1f}"
        lines.append(
            f"| {row['scenario']} | {row['scenario_kind']} | {row['status']} | "
            f"{row.get('record_count')} | {row.get('wall_seconds')} | {rate_text} | "
            f"{delta_mib} | {row.get('temporary_publish_bytes_written')} | "
            f"{row.get('private_spool_bytes_written')} | "
            f"{row.get('temporary_bytes_written')} | "
            f"{row.get('temporary_bytes_left')} | "
            f"{row.get('intermediate_jsonl_bytes')} | {final_kib} |"
        )
    comparison = payload.get("w1_w3_comparison") or {}
    lines += [
        "",
        f"Memory facts (assessment: {comparison.get('assessment')}): "
        f"record ratio {comparison.get('record_count_ratio')} · "
        f"peak RSS ratio {comparison.get('peak_rss_ratio')} · "
        f"peak Δ ratio {comparison.get('peak_rss_delta_ratio')}.",
        "",
        "W12 is functional cleanup evidence (`functional_cleanup`), not a "
        "throughput claim.  `intermediate_jsonl_bytes` is 0 for every "
        "scenario.  These are MEASURED EVIDENCE, not a regression baseline "
        "and not an optimization claim.  No Direct Write RSS regression "
        "threshold has been established by this first measured profile.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "evidence",
    )
    args = parser.parse_args(argv)
    counts = dict(FULL_COUNTS if args.mode == "full" else SMOKE_COUNTS)
    # Deterministic cleanup: the scenario workspace (DBF/FPT/staging/spool)
    # is transient and removed when this invocation exits — handled failure
    # included — while the JSON/Markdown evidence survives in --out.
    with tempfile.TemporaryDirectory(prefix="dbfbridge-dw-profile-") as scenario_root:
        payload = build_artifact(args.mode, counts, Path(scenario_root))
    problems = validate_artifact(payload)
    payload["validation_problems"] = problems
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"direct-write-v1-{args.mode}.json"
    md_path = args.out / f"direct-write-v1-{args.mode}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown_summary(payload), encoding="utf-8")
    for row in payload["scenarios"]:
        print(
            f"{row['scenario']}: {row['status']} records={row.get('record_count')} "
            f"wall={row.get('wall_seconds')}s peak_rss={row.get('peak_rss_bytes')} "
            f"spool={row.get('private_spool_bytes_written')} "
            f"jsonl={row.get('intermediate_jsonl_bytes')} "
            f"residue={row.get('temporary_bytes_left')}"
        )
    print(f"artifact: {json_path}")
    print(f"validation: {problems if problems else 'OK'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
