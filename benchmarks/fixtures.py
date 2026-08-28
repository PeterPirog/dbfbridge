"""Deterministic DBF/FPT fixture generators for the Phase 0 benchmark runner.

Fixtures are generated with a fixed content recipe, are byte-stable for a given
record count / deleted fraction / memo config, and are written OUTSIDE the
measured window.  They live in ``benchmark-data/`` and are never committed.

Versioning & validation
-----------------------
Every generated fixture is written together with a ``<name>.dbf.meta.json``
sidecar carrying:

- ``generator_version`` (bump when the recipe changes);
- ``kind`` (flat / memo / deleted / encoding) and ``encoding`` (for encoding
  fixtures);
- expected ``records`` and ``deleted`` counts;
- the memo config (for memo-heavy fixtures);
- ``dbf_sha256`` / ``fpt_sha256`` and byte sizes.

``ensure_fixture`` validates an existing fixture against its sidecar (generator
version, record/deleted counts, DBF+FPT presence, sizes and SHA-256) and
**safely regenerates it outside the measured window** when anything does not
match.  Reuse is therefore never a blind ``if path.exists()``.

Polish encodings
----------------
The ``encoding_*`` fixtures store genuine Polish diacritics (e.g. ``ąęłóńśćźż``)
as the raw bytes of the *target* codepage, so forcing that codepage at export
time reproduces the exact logical text.  Each codepage has its own dedicated,
correctly-labelled fixture.
"""

from __future__ import annotations

import hashlib
import json
import struct
from datetime import datetime
from pathlib import Path

# Bump whenever the content recipe changes; triggers safe regeneration.
SPEC_VERSION = "6"

# Logical text with real Polish diacritics (representable in cp1250, cp852 and
# Mazovia).  Used by the encoding fixtures so the forced-encoding code path is
# exercised on non-ASCII bytes, not just ASCII.
POLISH_TEXT = [
    "Żółw ąęłóńśćźż",
    "Książka ą ę ł ó",
    "Śliwka ć ź ż",
    "Polska ń ó ś",
    "Cząstka ą ę ł",
    "Miód ż ó",
]

MEMO_TEXT = (
    "Mazowska tresc benchmarkowa z polskimi znakami: żółw, książka, śliwka, miód. "
    "Linia z przecinkami i cudzyslown: 'cytat'. "
)


def _spec(name: str, type_: str, length: int, decimal: int) -> str:
    upper = type_.upper()
    if upper in {"M", "L", "D", "T", "Y"}:
        return f"{name} {type_}"
    if upper == "C":
        return f"{name} {type_}({length})"
    return f"{name} {type_}({length},{decimal})"


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FixtureIntegrityError(ValueError):
    """A DBF file fails a raw-layout integrity check (truncated, bad markers)."""


def _scan_dbf(dbf_path: Path) -> dict[str, int]:
    """Strictly scan a DBF record area and return measured counts.

    The scan is **strict**: it raises :class:`FixtureIntegrityError` when the
    layout is inconsistent instead of silently guessing a count.  Rules:

    - the header must be at least 32 bytes with a known DBF version byte;
    - ``header_length`` must be plausible (>= 33, within the file size);
    - ``record_length`` must be >= 2 (delete marker + at least one byte);
    - every record in the declared record area is scanned: a record shorter
      than ``record_length`` is rejected, and a delete marker that is neither
      ``0x20`` (active) nor ``0x2A`` (deleted) is rejected;
    - the number of scanned records must equal the header record count.

    Counting is done **without decoding any text field**, so it works for any
    codepage (including the cp1250/cp852/Mazovia encoding fixtures).
    """

    with dbf_path.open("rb") as infile:
        head = infile.read(32)
        if len(head) < 32:
            raise FixtureIntegrityError(f"{dbf_path.name}: header shorter than 32 bytes")
        if head[0] not in (0x02, 0x03, 0x30, 0x31, 0x83, 0x87):
            raise FixtureIntegrityError(
                f"{dbf_path.name}: unknown DBF version byte 0x{head[0]:02x}"
            )
        total = int.from_bytes(head[4:8], "little")
        header_len = int.from_bytes(head[8:10], "little")
        record_size = int.from_bytes(head[10:12], "little")
        if record_size < 2:
            raise FixtureIntegrityError(f"{dbf_path.name}: implausible record_length {record_size}")
        if header_len < 33:
            raise FixtureIntegrityError(f"{dbf_path.name}: implausible header_length {header_len}")

        size = infile.seek(0, 2)
        if total > 0 and header_len + total * record_size > size:
            raise FixtureIntegrityError(
                f"{dbf_path.name}: record area truncated "
                f"(need {header_len + total * record_size} bytes, file has {size})"
            )

        infile.seek(header_len)  # record area starts after the field descriptors
        active = 0
        deleted = 0
        scanned = 0
        for _ in range(total):
            flag = infile.read(1)
            if not flag:
                raise FixtureIntegrityError(
                    f"{dbf_path.name}: truncated record area at record {scanned + 1}"
                )
            if flag == b"*":
                deleted += 1
            elif flag == b" ":
                active += 1
            else:
                raise FixtureIntegrityError(
                    f"{dbf_path.name}: invalid delete marker 0x{flag[0]:02x} at record {scanned + 1}"
                )
            rest = infile.read(record_size - 1)
            if len(rest) != record_size - 1:
                raise FixtureIntegrityError(
                    f"{dbf_path.name}: record {scanned + 1} shorter than record_length {record_size}"
                )
            scanned += 1
        if scanned != total:
            raise FixtureIntegrityError(
                f"{dbf_path.name}: scanned {scanned} records but header declares {total}"
            )
    return {"active_records": active, "deleted_records": deleted, "total_records": active + deleted}


def _write_meta(path: Path, meta: dict[str, object]) -> None:
    meta_path = path.with_suffix(".meta.json")
    meta["dbf_sha256"] = _sha256(path)
    meta["dbf_bytes"] = path.stat().st_size
    fpt = path.with_suffix(".fpt")
    meta["fpt_present"] = fpt.is_file()
    meta["fpt_sha256"] = _sha256(fpt)
    meta["fpt_bytes"] = fpt.stat().st_size if fpt.is_file() else 0
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _measured_counts(path: Path) -> dict[str, int]:
    """Record counts measured from the file on disk (never an expectation).

    Raises :class:`FixtureIntegrityError` when the file does not pass the
    strict raw-layout scan.
    """

    return _scan_dbf(path)


def _meta_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _validate(path: Path, expected: dict[str, object]) -> bool:
    """Return True when the on-disk fixture matches *expected*.

    Checks sidecar presence, generator version, kind/encoding, DBF presence,
    DBF (+FPT where required) SHA-256, and FPT presence/absence.  The
    **measured** counts (strict raw-layout scan) are compared directly against
    the spec:

    - ``total_records``  == expected ``records``;
    - ``deleted_records`` == expected ``deleted``;
    - ``active_records`` == expected ``records`` - expected ``deleted``;
    - ``active_records + deleted_records == total_records``.
    """

    meta_file = _meta_path(path)
    if not meta_file.is_file() or not path.is_file():
        return False
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for key, want in expected.items():
        if meta.get(key) != want:
            return False
    if meta.get("dbf_sha256") != _sha256(path):
        return False
    # The file must pass the strict scan, and the measured counts must match
    # the specification (not merely the sidecar's claims).
    try:
        measured = _measured_counts(path)
    except FixtureIntegrityError:
        return False
    expected_records = int(expected.get("records", 0))  # type: ignore[arg-type]
    expected_deleted = int(expected.get("deleted", 0))  # type: ignore[arg-type]
    if (
        measured["total_records"] != expected_records
        or measured["deleted_records"] != expected_deleted
        or measured["active_records"] != expected_records - expected_deleted
        or measured["active_records"] + measured["deleted_records"] != measured["total_records"]
    ):
        return False
    # The sidecar must record exactly what was measured (no drift).
    if any(meta.get(key) != value for key, value in measured.items()):
        return False
    fpt = path.with_suffix(".fpt")
    if expected.get("require_fpt"):
        if (
            not fpt.is_file()
            or not meta.get("fpt_present")
            or meta.get("fpt_sha256") != _sha256(fpt)
        ):
            return False
    else:
        if fpt.is_file() or meta.get("fpt_present"):
            return False
    return True


def _ensure(
    path: Path,
    expected: dict[str, object],
    generate,
) -> Path:
    """Reuse a valid fixture, otherwise (re)generate it and write the sidecar.

    After generation the fixture is **fully validated again**: a freshly
    generated but inconsistent fixture raises :class:`FixtureIntegrityError`
    instead of being returned as good.
    """

    if _validate(path, expected):
        return path
    for existing in (path, path.with_suffix(".fpt"), _meta_path(path)):
        if existing.is_file():
            existing.unlink()
    generate(path)
    meta = dict(expected)
    meta.update(_measured_counts(path))
    _write_meta(path, meta)
    if not _validate(path, expected):
        raise FixtureIntegrityError(f"{path.name}: freshly generated fixture failed validation")
    return path


# --------------------------------------------------------------------------- flat


def _flat_fields() -> list[tuple[str, str, int, int]]:
    return [
        ("ID", "N", 12, 0),
        ("NAZWA", "C", 40, 0),
        ("MIASTO", "C", 24, 0),
        ("KWOTA", "N", 12, 2),
        ("LICZBA", "Y", 8, 0),
        ("AKTYWNY", "L", 1, 0),
        ("DATA", "D", 8, 0),
        ("DATA_CZAS", "T", 8, 0),
    ]


def _generate_flat(
    path: Path,
    records: int,
    *,
    deleted_fraction: float = 0.0,
) -> None:
    import dbf

    path.parent.mkdir(parents=True, exist_ok=True)
    table = dbf.Table(
        str(path),
        field_specs="; ".join(_spec(*spec) for spec in _flat_fields()),
        dbf_type="vfp",
        codepage=0xC8,  # cp1250 language driver; encoding fixtures set their own
    )
    table.open(mode=dbf.READ_WRITE)
    base_dt = datetime(2020, 1, 1, 8, 30, 15)
    deleted_set: set[int] = set()
    if deleted_fraction:
        # Deterministic deleted set: every 10th record up to the fraction.
        target = max(1, int(records * deleted_fraction))
        deleted_set = {i for i in range(10, records + 1, 10) if i // 10 <= target}
    for i in range(1, records + 1):
        table.append(
            {
                "ID": i,
                "NAZWA": f"{POLISH_TEXT[i % len(POLISH_TEXT)]} {i}",
                "MIASTO": POLISH_TEXT[(i * 7) % len(POLISH_TEXT)][:12],
                "KWOTA": (i * 37) % 100000 / 100,
                "LICZBA": (i % 997) / 7,
                "AKTYWNY": i % 3 != 0,
                "DATA": date_from_ordinal(737425 + (i % 1500)),
                "DATA_CZAS": None if i % 11 == 0 else base_dt,
            }
        )
        if i in deleted_set:
            dbf.delete(table[-1])
    table.close()


def date_from_ordinal(ordinal: int):
    from datetime import date

    return date.fromordinal(ordinal)


def generate_flat(
    path: Path,
    records: int,
    *,
    deleted_fraction: float = 0.0,
) -> Path:
    """Wide, memo-free table (no memo field, no FPT) exercising C/N/L/D/T/Y parsing."""

    expected = {
        "generator_version": SPEC_VERSION,
        "kind": "flat",
        "records": records,
        "deleted": max(1, int(records * deleted_fraction)) if deleted_fraction else 0,
        "deleted_fraction": deleted_fraction,
        "require_fpt": False,
    }
    return _ensure(
        path,
        expected,
        lambda p: _generate_flat(p, records, deleted_fraction=deleted_fraction),
    )


# --------------------------------------------------------------------------- memo


def _generate_memo_heavy(path: Path, records: int, memo_chars: int) -> None:
    import dbf

    path.parent.mkdir(parents=True, exist_ok=True)
    table = dbf.Table(
        str(path),
        field_specs="; ".join(
            _spec(*spec)
            for spec in [("ID", "N", 10, 0), ("NAZWA", "C", 30, 0), ("NOTATKA", "M", 0, 0)]
        ),
        dbf_type="vfp",
        codepage=0xC8,
    )
    table.open(mode=dbf.READ_WRITE)
    memo_body = (MEMO_TEXT * (memo_chars // len(MEMO_TEXT) + 1))[:memo_chars]
    for i in range(1, records + 1):
        table.append(
            {
                "ID": i,
                "NAZWA": f"{POLISH_TEXT[i % len(POLISH_TEXT)]} {i}",
                "NOTATKA": None if i % 10 == 0 else memo_body,
            }
        )
    table.close()


def generate_memo_heavy(
    path: Path,
    records: int,
    *,
    memo_chars: int = 4000,
) -> Path:
    """Table where most of the payload lives in FPT memo blocks."""

    expected = {
        "generator_version": SPEC_VERSION,
        "kind": "memo",
        "records": records,
        "deleted": 0,
        "memo_chars": memo_chars,
        "require_fpt": True,
    }
    return _ensure(path, expected, lambda p: _generate_memo_heavy(p, records, memo_chars))


# --------------------------------------------------------------------------- encoding


def _write_minimal_dbf(
    path: Path,
    encoding: str,
    text: str,
) -> None:
    """Hand-write a minimal one-field DBF whose stored bytes are
    ``text.encode(encoding)``.  Bypasses any codepage mapping so the target
    codepage bytes are exact, letting the forced-encoding export reproduce the
    logical text cleanly under strict decoding."""

    # Ensure dbfbridge's custom Polish codecs (Mazovia/PIAST) are registered
    # with codecs so ``text.encode(encoding)`` works for non-builtin names.
    try:
        from dbf_bridge.exporter.polish_codecs import register_polish_codecs

        register_polish_codecs()
    except Exception:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    for existing in (path, path.with_suffix(".fpt")):
        if existing.is_file():
            existing.unlink()

    field_length = max(1, len(text.encode(encoding)))
    header_length = 32 + 32 + 1  # header + one field descriptor + 0x0d terminator
    record_length = 1 + field_length  # delete marker + C field

    now = datetime(2020, 1, 1)
    header = struct.pack(
        "<BBBBLHH20x",
        0x03,  # dbf version (dBase III without memo)
        now.year - 1900,  # year byte: years since 1900
        now.month,
        now.day,
        1,  # numrecords
        header_length,
        record_length,
    )
    name = b"TEKST".ljust(11, b"\x00")
    field_desc = name + b"C" + b"\x00\x00\x00\x00" + bytes([field_length, 0]) + b"\x00" * 14
    terminator = b"\x0d"
    record = b" " + text.encode(encoding).ljust(field_length, b"\x20")

    with path.open("wb") as outfile:
        outfile.write(header + field_desc + terminator + record + b"\x1a")
        outfile.flush()


def generate_encoding(path: Path, encoding: str) -> Path:
    """Dedicated fixture for a forced-encoding scenario.

    Stores ``POLISH_TEXT`` (real diacritics) as the raw bytes of *encoding*, so
    exporting with ``--encoding <encoding>`` yields the exact logical text.
    The fixture holds exactly one record (the joined logical text); there is no
    record-count parameter because the single-record recipe is fixed.
    """

    text = " ".join(POLISH_TEXT)
    expected = {
        "generator_version": SPEC_VERSION,
        "kind": "encoding",
        "records": 1,
        "deleted": 0,
        "encoding": encoding,
        "text": text,
        "require_fpt": False,
    }

    def build(p: Path) -> None:
        _write_minimal_dbf(p, encoding, text)

    return _ensure(path, expected, build)


# --------------------------------------------------------------------------- manifest


def fixture_manifest(fixture_dir: Path) -> dict[str, object]:
    manifest: dict[str, object] = {"directory": str(fixture_dir)}
    if fixture_dir.is_dir():
        for path in sorted(fixture_dir.rglob("*")):
            if path.is_file() and path.suffix != ".meta.json":
                manifest[path.as_posix()] = path.stat().st_size
    return manifest


__all__ = [
    "SPEC_VERSION",
    "POLISH_TEXT",
    "FixtureIntegrityError",
    "generate_flat",
    "generate_memo_heavy",
    "generate_encoding",
    "fixture_manifest",
    "_sha256",
    "_scan_dbf",
    "_measured_counts",
]
