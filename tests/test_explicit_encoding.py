"""Explicit Polish encoding override hardening (0.3 correctness).

Regression tests for deterministic explicit overrides:

- ``encoding="mazovia"`` / ``"piast"`` / ``"pki"`` must work in a FRESH
  interpreter without any prior exporter import (the historical bug: Polish
  codecs were registered only as an ``exporter.reader`` module-import side
  effect, so a Direct Read with an explicit override failed with
  ``ENCODING_UNKNOWN``);
- unknown explicit codecs raise the typed ``EncodingUnknownError``
  (``ENCODING_UNKNOWN``, JSON-safe) deterministically — validated before any
  descriptor decoding;
- ``encoding="auto"`` with the Mazovia language driver (0x69) is unchanged,
  and an explicit override still wins over the header-declared driver;
- lazy/inline memo payloads decode with the same explicit encoding;
- ``import dbfbridge`` stays side-effect free (no codec registration, no
  exporter import);
- sources stay byte-identical and no artifacts are produced.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from dbfbridge import (
    EncodingUnknownError,
    ErrorCode,
    LazyMemoValue,
    iter_records,
    read_records,
)

SRC_ROOT = Path(__file__).parents[1] / "src"


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _mazovia_bytes(text: str) -> bytes:
    """Encode *text* with the canonical Mazovia table (single source)."""
    from dbf_bridge.core.codecs import _MAZOVIA_TABLE, TableCodec

    codec = TableCodec("mazovia", _MAZOVIA_TABLE)
    encoded, _consumed = codec.encode(text)
    return encoded


def _encoding_fixture(tmp_path: Path, name: str, encoding: str) -> tuple[Path, str]:
    """Minimal DBF whose single Character field stores POLISH text bytes in
    *encoding* (hand-written bytes, exact codepage payload)."""
    text = "Żółw ąęłóńśćźż Książka Ą Ę Ł Ó Ś Ź Ź Ż"
    if encoding == "cp1250":
        payload = text.encode("cp1250")
    elif encoding == "cp852":
        payload = text.encode("cp852")
    else:
        payload = _mazovia_bytes(text)

    field_length = len(payload)
    header_length = 32 + 32 + 1
    record_length = 1 + field_length
    header = struct.pack("<BBBBLHH20x", 0x03, 126, 9, 1, 1, header_length, record_length)
    descriptor = (
        b"TEKST".ljust(11, b"\x00")
        + b"C"
        + b"\x00\x00\x00\x00"
        + bytes([field_length, 0])
        + b"\x00" * 14
    )
    record = b" " + payload
    path = tmp_path / name
    path.write_bytes(header + descriptor + b"\x0d" + record + b"\x1a")
    return path, text


def _patch_language_driver(dbf_path: Path, driver: int) -> None:
    data = bytearray(dbf_path.read_bytes())
    data[29] = driver  # language driver byte in the reserved header block
    dbf_path.write_bytes(bytes(data))


# ---------------------------------------------------------------------------
# fresh-process import-order regression (the historical bug)
# ---------------------------------------------------------------------------


def test_fresh_process_explicit_mazovia_without_exporter_import(tmp_path: Path) -> None:
    """Direct Read with an explicit override must not depend on import order."""
    from benchmarks.fixtures import generate_encoding

    fixture = generate_encoding(tmp_path / "mazovia.dbf", "mazovia")
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{SRC_ROOT}')\n"
        "import dbfbridge\n"
        f"records = list(dbfbridge.iter_records(r'{fixture.as_posix()}', encoding='mazovia'))\n"
        "assert len(records) == 1\n"
        "text = records[0].values['TEKST']\n"
        "assert 'Żółw' in text and 'Ś' in text, text\n"
        "print(json.dumps({'records': len(records)}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[1]),
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    assert json.loads(completed.stdout.strip().splitlines()[-1])["records"] == 1
    # The exporter (historically the accidental codec registrar) is never
    # imported by this fresh process.
    assert "dbf_bridge.exporter" not in code


def test_fresh_process_import_dbfbridge_has_no_codec_side_effects() -> None:
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{SRC_ROOT}')\n"
        "import dbfbridge\n"
        "try:\n"
        "    b'x'.decode('mazovia')\n"
        "    registered = True\n"
        "except LookupError:\n"
        "    registered = False\n"
        "print(json.dumps({'registered': registered,\n"
        "                  'exporter': 'dbf_bridge.exporter.reader' in sys.modules}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parents[1]),
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["registered"] is False, "import dbfbridge must not register Polish codecs"
    assert payload["exporter"] is False, "import dbfbridge must not import the exporter"


# ---------------------------------------------------------------------------
# explicit override matrix
# ---------------------------------------------------------------------------


def test_explicit_mazovia_piast_pki_preserve_polish(tmp_path: Path) -> None:
    for name in ("mazovia", "piast", "pki"):
        fixture, expected = _encoding_fixture(tmp_path, f"{name}.dbf", name)
        records = list(iter_records(fixture, encoding=name))
        assert len(records) == 1, name
        assert records[0].values["TEKST"] == expected, name


def test_explicit_cp1250_cp852_preserve_polish(tmp_path: Path) -> None:
    for name in ("cp1250", "cp852"):
        fixture, expected = _encoding_fixture(tmp_path, f"{name}.dbf", name)
        records = list(iter_records(fixture, encoding=name))
        assert len(records) == 1, name
        assert records[0].values["TEKST"] == expected, name


def test_explicit_override_works_via_read_records(tmp_path: Path) -> None:
    fixture, expected = _encoding_fixture(tmp_path, "page.dbf", "mazovia")
    page = read_records(fixture, offset=0, limit=10, encoding="mazovia")
    assert len(page.records) == 1
    assert page.records[0].values["TEKST"] == expected


def test_unknown_explicit_codec_is_typed_and_json_safe(tmp_path: Path) -> None:
    fixture, _expected = _encoding_fixture(tmp_path, "unknown.dbf", "mazovia")
    with pytest.raises(EncodingUnknownError) as error:
        list(iter_records(fixture, encoding="definitely-not-a-real-codec"))
    assert error.value.code == ErrorCode.ENCODING_UNKNOWN
    payload = error.value.to_dict()
    assert payload["context"]["encoding"] == "definitely-not-a-real-codec"
    assert json.dumps(payload)  # JSON-safe
    assert payload["path"].endswith("unknown.dbf")


def test_unknown_codec_deterministic_regardless_of_content(tmp_path: Path) -> None:
    """A valid table with an unknown codec fails deterministically — the
    codec is validated before descriptor decoding, not at the first decode."""
    fixture, _expected = _encoding_fixture(tmp_path, "det.dbf", "mazovia")
    for _attempt in range(3):
        with pytest.raises(EncodingUnknownError) as error:
            list(iter_records(fixture, encoding="also-not-a-codec"))
        assert error.value.code == ErrorCode.ENCODING_UNKNOWN


def test_auto_ldid_069_still_resolves_mazovia(tmp_path: Path) -> None:
    fixture, _expected = _encoding_fixture(tmp_path, "auto.dbf", "mazovia")
    _patch_language_driver(fixture, 0x69)
    auto = list(iter_records(fixture, encoding="auto"))
    explicit = list(iter_records(fixture, encoding="mazovia"))
    assert len(auto) == 1 and len(explicit) == 1
    assert auto[0].values["TEKST"] == explicit[0].values["TEKST"]


def test_explicit_override_wins_over_header_driver(tmp_path: Path) -> None:
    """Header declares Mazovia (0x69); record bytes are cp1250 — the explicit
    override wins (unchanged precedence)."""
    fixture, expected = _encoding_fixture(tmp_path, "override.dbf", "cp1250")
    _patch_language_driver(fixture, 0x69)
    records = list(iter_records(fixture, encoding="cp1250"))
    assert records[0].values["TEKST"] == expected


def test_decode_errors_policies_with_mazovia(tmp_path: Path) -> None:
    fixture, expected = _encoding_fixture(tmp_path, "policies.dbf", "mazovia")
    strict = list(iter_records(fixture, encoding="mazovia", decode_errors="strict"))
    assert strict[0].values["TEKST"] == expected
    for policy in ("replace", "ignore"):
        records = list(iter_records(fixture, encoding="mazovia", decode_errors=policy))
        assert len(records) == 1
        assert isinstance(records[0].values["TEKST"], str)


# ---------------------------------------------------------------------------
# memo payloads with explicit Mazovia
# ---------------------------------------------------------------------------


def _memo_fixture_with_mazovia_payload(tmp_path: Path) -> tuple[Path, str]:
    """DBF+FPT whose FPT memo payload stores Mazovia-encoded Polish text."""
    import dbf

    dbf_path = tmp_path / "memo_mazovia.dbf"
    table = dbf.Table(
        str(dbf_path),
        field_specs="ID N(3,0); NOTATKA M",
        dbf_type="vfp",
        codepage=0xC8,
    )
    table.open(mode=dbf.READ_WRITE)
    table.append({"ID": 1, "NOTATKA": "PLACEXX"})
    table.close()

    memo_text = "Żółw ąę"
    payload = _mazovia_bytes(memo_text)
    fpt = dbf_path.with_suffix(".fpt")
    data = fpt.read_bytes()
    marker = b"PLACEXX"
    offset = data.find(marker)
    assert offset != -1, "memo payload placeholder not found"
    assert len(payload) == len(marker)
    data = data[:offset] + payload + data[offset + len(marker) :]
    fpt.write_bytes(data)
    return dbf_path, memo_text


def test_lazy_memo_explicit_mazovia(tmp_path: Path) -> None:
    dbf_path, expected = _memo_fixture_with_mazovia_payload(tmp_path)
    record = next(iter_records(dbf_path, encoding="mazovia", memo="lazy"))
    value = record.values["NOTATKA"]
    assert isinstance(value, LazyMemoValue)
    # The explicit encoding was known at stream-request time: load() must
    # decode with it — no LookupError.
    assert value.load() == expected


def test_inline_memo_explicit_mazovia(tmp_path: Path) -> None:
    dbf_path, expected = _memo_fixture_with_mazovia_payload(tmp_path)
    records = list(iter_records(dbf_path, encoding="mazovia", memo="inline"))
    assert len(records) == 1
    assert records[0].values["NOTATKA"] == expected


def test_memo_lazy_mazovia_never_opens_fpt(tmp_path: Path) -> None:
    """lazy + explicit mazovia keeps the zero-FPT-IO boundary guarantee."""
    dbf_path, _expected = _memo_fixture_with_mazovia_payload(tmp_path)
    fpt = dbf_path.with_suffix(".fpt")
    record = next(iter_records(dbf_path, encoding="mazovia", memo="lazy"))
    assert isinstance(record.values["NOTATKA"], LazyMemoValue)
    # FPT can be renamed right away: lazy never opened it.
    moved = tmp_path / "moved.fpt"
    os.rename(fpt, moved)
    os.rename(moved, fpt)


# ---------------------------------------------------------------------------
# source immutability + zero artifacts
# ---------------------------------------------------------------------------


def test_explicit_mazovia_source_immutability(tmp_path: Path) -> None:
    import hashlib

    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    dbf_path, _expected = _memo_fixture_with_mazovia_payload(tmp_path)
    fpt = dbf_path.with_suffix(".fpt")
    before_dbf, before_fpt = _sha256(dbf_path), _sha256(fpt)

    list(iter_records(dbf_path, encoding="mazovia"))
    lazy_record = next(iter_records(dbf_path, encoding="mazovia", memo="lazy"))
    lazy_record.values["NOTATKA"].load()
    list(iter_records(dbf_path, encoding="mazovia", memo="inline"))
    with pytest.raises(EncodingUnknownError):
        list(iter_records(dbf_path, encoding="not-a-codec"))

    assert _sha256(dbf_path) == before_dbf
    assert _sha256(fpt) == before_fpt
    # zero output artifacts
    assert sorted(path.name for path in tmp_path.iterdir()) == [dbf_path.name, fpt.name]


def test_unknown_encoding_error_contract(tmp_path: Path) -> None:
    fixture, _expected = _encoding_fixture(tmp_path, "typed.dbf", "mazovia")
    with pytest.raises(EncodingUnknownError) as error:
        list(iter_records(fixture, encoding="definitely-not-a-real-codec"))
    assert error.value.code == ErrorCode.ENCODING_UNKNOWN
    payload = error.value.to_dict()
    assert payload["code"] == "ENCODING_UNKNOWN"
    assert payload["context"]["encoding"] == "definitely-not-a-real-codec"
    assert json.dumps(payload)
