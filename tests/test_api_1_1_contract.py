"""Normative dbfbridge v1.1 public API contract (additive Direct Write).

Protects the promoted public Direct Write surface on top of the protected
1.0 baseline (which stays a subset contract — see
``tests/test_api_1_0_contract.py``):

- both public facades export ``write_table``, ``WriteResult``, the
  ``DirectWriteError`` family and the write ``ErrorCode`` vocabulary with
  strict alias parity (``dbfbridge.X is dbf_bridge.X``);
- the exact normative ``write_table`` signature (keyword-only,
  ``overwrite=False``, ``progress``/``cancel_check``);
- the stable ``WriteResult`` field set and JSON-safe ``to_dict()``;
- error-family separation from ``DirectReadError`` and JSON-safe payloads;
- lazy root import (``dbf`` never loads; the physical backend is not
  eager-imported either);
- the protected nine 1.0 operations remain present.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

import dbf_bridge
import dbfbridge

REPO_ROOT = Path(__file__).parents[1]

WRITE_OPERATIONS = ("write_table",)
WRITE_RESULT_MODELS = ("WriteResult",)
WRITE_ERROR_CLASSES = (
    "DirectWriteError",
    "DestinationIoError",
    "WriteCancelledError",
    "WriteFieldUnsupportedError",
    "WriteMemoFailedError",
    "WritePublicationFailedError",
    "WriteSchemaInvalidError",
    "WriteValueInvalidError",
)
WRITE_ERROR_CODES = (
    "DESTINATION_IO_ERROR",
    "WRITE_CANCELLED",
    "WRITE_FIELD_UNSUPPORTED",
    "WRITE_MEMO_FAILED",
    "WRITE_PUBLICATION_FAILED",
    "WRITE_SCHEMA_INVALID",
    "WRITE_VALUE_INVALID",
)
PROTECTED_1_0_OPERATIONS = (
    "inspect_table",
    "read_schema",
    "iter_records",
    "read_records",
    "iter_raw_records",
    "export_dbf",
    "reconstruct_dbf",
    "verify_conversion",
    "check_conversion_quality",
)


# ---------------------------------------------------------------------------
# public exports + alias parity (DBFB-TEST-001)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", (*WRITE_OPERATIONS, *WRITE_RESULT_MODELS))
def test_write_surface_is_public_from_both_facades(name: str) -> None:
    for facade in (dbfbridge, dbf_bridge):
        assert name in facade.__all__, (name, "missing from __all__")
        assert getattr(facade, name) is not None


@pytest.mark.parametrize("name", WRITE_ERROR_CLASSES)
def test_write_error_family_is_public_from_both_facades(name: str) -> None:
    for facade in (dbfbridge, dbf_bridge):
        assert name in facade.__all__, (name, "missing from __all__")
        assert getattr(facade, name) is not None


@pytest.mark.parametrize("name", WRITE_OPERATIONS)
def test_alias_identity(name: str) -> None:
    assert getattr(dbfbridge, name) is getattr(dbf_bridge, name)


@pytest.mark.parametrize("name", (*WRITE_RESULT_MODELS, *WRITE_ERROR_CLASSES))
def test_alias_identity_models_and_errors(name: str) -> None:
    assert getattr(dbfbridge, name) is getattr(dbf_bridge, name)


def test_write_error_codes_are_in_the_canonical_vocabulary() -> None:
    codes = {member.value for member in dbfbridge.ErrorCode}
    assert set(WRITE_ERROR_CODES) <= codes
    # alias parity for the vocabulary itself
    assert dbfbridge.ErrorCode is dbf_bridge.ErrorCode


# ---------------------------------------------------------------------------
# exact normative signature (DBFB-SEMVER-005)
# ---------------------------------------------------------------------------


def test_write_table_signature_matches_the_architecture() -> None:
    signature = inspect.signature(dbfbridge.write_table)
    assert list(signature.parameters) == [
        "destination",
        "schema",
        "records",
        "overwrite",
        "staging_directory",
        "progress",
        "cancel_check",
    ]
    kinds = {name: parameter.kind for name, parameter in signature.parameters.items()}
    assert kinds["destination"] is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for keyword_only in ("schema", "records", "overwrite", "staging_directory", "progress", "cancel_check"):
        assert kinds[keyword_only] is inspect.Parameter.KEYWORD_ONLY, keyword_only
    assert signature.parameters["overwrite"].default is False
    assert signature.parameters["staging_directory"].default is None
    assert signature.parameters["progress"].default is None
    assert signature.parameters["cancel_check"].default is None


# ---------------------------------------------------------------------------
# WriteResult stability (JSON-safe boundary)
# ---------------------------------------------------------------------------


def test_write_result_fields_and_json_boundary() -> None:
    from dbf_bridge.write import WriteResult as _Implementation

    assert dbfbridge.WriteResult is _Implementation
    field_names = {field.name for field in _Implementation.__dataclass_fields__.values()}
    assert field_names == {
        "destination",
        "fpt_path",
        "fpt_published",
        "records_written",
        "deleted_records",
        "structural_cdx",
        "index_rebuild_required",
        "dbc_bound",
        "dbf_sha256",
        "fpt_sha256",
        "warnings",
    }
    result = _Implementation(
        destination=Path("C:/data/out.dbf"),
        fpt_path=Path("C:/data/out.fpt"),
        fpt_published=True,
        records_written=3,
        deleted_records=1,
        structural_cdx=True,
        index_rebuild_required=True,
        dbc_bound=False,
        dbf_sha256="a" * 64,
        fpt_sha256="b" * 64,
        warnings=("rebuild required",),
    )
    payload = result.to_dict()
    json.dumps(payload)  # JSON-safe: never raises
    assert payload["destination"] == "C:/data/out.dbf"
    assert isinstance(payload["warnings"], list)
    assert payload["records_written"] == 3 and payload["deleted_records"] == 1


def test_write_result_is_immutable() -> None:
    from dbf_bridge.write import WriteResult as _Implementation

    result = _Implementation(
        destination=Path("x.dbf"),
        fpt_path=None,
        fpt_published=False,
        records_written=0,
        deleted_records=0,
        structural_cdx=False,
        index_rebuild_required=False,
        dbc_bound=False,
        dbf_sha256="0" * 64,
        fpt_sha256=None,
        warnings=(),
    )
    with pytest.raises(AttributeError):  # frozen dataclass: assignment raises
        result.records_written = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# error family separation + JSON-safe boundary (DBFB-TEST-016)
# ---------------------------------------------------------------------------


def test_direct_write_error_family_is_independent_from_direct_read() -> None:
    from dbf_bridge.core.errors import DirectReadError

    assert not issubclass(dbfbridge.DirectWriteError, DirectReadError)
    for name in WRITE_ERROR_CLASSES:
        cls = getattr(dbfbridge, name)
        assert issubclass(cls, dbfbridge.DirectWriteError)
        assert not issubclass(cls, DirectReadError)


@pytest.mark.parametrize(
    "name",
    [name for name in WRITE_ERROR_CLASSES if name != "DirectWriteError"],
)
def test_every_public_write_error_is_json_safe(name: str) -> None:
    # The family base is the inheritance root (no code of its own, exactly
    # like the DirectReadError root); every CONCRETE public subclass carries
    # a stable ErrorCode and a JSON-safe payload.
    cls = getattr(dbfbridge, name)
    error = cls("boom", path="out/x.dbf", context={"field": "CODE", "dbf_type": "C"})
    payload = error.to_dict()
    json.dumps(payload)  # never raises
    assert set(payload) >= {"code", "message", "path", "context"}


@pytest.mark.parametrize("code", WRITE_ERROR_CODES)
def test_every_write_code_has_a_public_typed_error(code: str) -> None:
    mapped = {
        getattr(dbf_bridge.core.errors, name).code
        for name in WRITE_ERROR_CLASSES
        if hasattr(getattr(dbf_bridge.core.errors, name), "code")
    }
    values = {member.value for member in mapped if not isinstance(member, str)}
    values |= {member for member in mapped if isinstance(member, str)}
    assert code in values


# ---------------------------------------------------------------------------
# lazy import + optional dependency (DBFB-TEST-002)
# ---------------------------------------------------------------------------


def test_fresh_interpreter_root_import_stays_lazy() -> None:
    code = (
        "import sys;"
        "import dbfbridge;"
        "assert 'dbf' not in sys.modules, 'optional dbf dependency loaded';"
        "assert 'dbf_bridge.write.backend' not in sys.modules, 'backend eager-loaded';"
        "assert dbfbridge.write_table is not None;"
        "assert 'dbf' not in sys.modules, 'dbf loaded by write_table access';"
        "print('PASS')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout


def test_missing_write_extra_fails_typed_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbf_bridge import OptionalDependencyMissingError

    def _blocked(module_name: str, **kwargs: object) -> object:
        raise OptionalDependencyMissingError(
            dependency="dbf", extra="write", operation="write_table"
        )

    monkeypatch.setattr("dbf_bridge.write.api.require_optional", _blocked)
    destination = tmp_path / "fresh" / "blocked.dbf"
    from dbf_bridge.core.models import TableSchema

    minimal = TableSchema(
        path=Path("memory:fixture"),
        record_count=0,
        header_length=33,
        record_length=2,
        language_driver=3,
        encoding="cp1250",
        has_memo=False,
        has_memo_flag=False,
        has_structural_cdx=False,
        is_database_container=False,
        dbc_bound=False,
        dbc_backlink_path=None,
        table_flags=0,
        fields=(),  # empty: schema validation runs AFTER the dependency gate
        warnings=(),
        dbversion_byte=0x30,
        dbversion_name="Visual FoxPro",
        last_update=None,
        incomplete_transaction=False,
        encryption_flag=False,
        memo_companion_format=None,
        memo_companion_present=False,
        memo_companion_path=None,
        memo_companion_size_bytes=None,
        memo_block_size=None,
        memo_next_free_block=None,
        companion_cdx_present=False,
        companion_cdx_path=None,
    )
    with pytest.raises(OptionalDependencyMissingError):
        dbfbridge.write_table(destination, schema=minimal, records=iter(()))
    assert not destination.parent.exists()


# ---------------------------------------------------------------------------
# 1.0 preservation (subset, no duplication of the full 1.0 contract)
# ---------------------------------------------------------------------------


def test_protected_1_0_operations_remain_public() -> None:
    for name in PROTECTED_1_0_OPERATIONS:
        assert name in dbfbridge.__all__
        assert callable(getattr(dbfbridge, name))
        assert getattr(dbfbridge, name) is getattr(dbf_bridge, name)
