"""Mechanical 1.0 public API contract regression tests (docs/api-1.0.md).

These tests freeze PUBLIC behaviour only — the preferred import boundary, the
stable symbol surface, the nine public operations, the RawMode choices, the
architecture-required machine codes, and the JSON-safe run-result boundary.
They deliberately avoid snapshotting implementation internals so harmless
internal refactoring stays possible.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import dbf_bridge
import dbfbridge

# ---------------------------------------------------------------------------
# documented stable surface (docs/api-1.0.md §1-§4)
# ---------------------------------------------------------------------------

PREFERRED_IMPORT = "dbfbridge"

STABLE_OPERATIONS = (
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

STABLE_MODELS = (
    "TableInfo",
    "TableSchema",
    "FieldInfo",
    "DirectRecord",
    "RecordPage",
    "LazyMemoValue",
    "ExportOptions",
    "ExportRunResult",
    "ReconstructionOptions",
    "ReconstructionRunResult",
    "ReconstructionResult",
    "VerificationRunResult",
    "QualityRunResult",
    "TableResult",
    "TableStatus",
)

STABLE_ERRORS = (
    "ErrorCode",
    "DirectReadError",
    "DbfPathError",
    "DbfHeaderInvalidError",
    "DbfTruncatedError",
    "DbfFormatUnsupportedError",
    "DbfIoError",
    "DbfRecordInvalidError",
    "EncodingUnknownError",
    "TextDecodeError",
    "FptRequiredMissingError",
    "FptInvalidError",
    "ArgumentInvalidError",
    "FieldProjectionInvalidError",
    "FieldTypeUnsupportedError",
    "ReadCancelledError",
    "OperationError",
    "OperationArgumentError",
    "OperationPathError",
    "OperationOutputExistsError",
    "OptionalDependencyMissingError",
    "DBFBridgeRunError",
)

STABLE_POLICIES = (
    "RawMode",
    "MemoPolicy",
    "MissingMemoPolicy",
    "DecodeErrors",
    "DeletedPolicy",
    "OutputFormat",
    "InputFormat",
)

STABLE_PROGRESS = ("ProgressCallback", "ProgressEvent", "CancellationCheck")

# Architecture-required distinguishable machine codes (§17/§23).
REQUIRED_ERROR_CODES = (
    "DBF_FORMAT_UNSUPPORTED",
    "DBF_HEADER_INVALID",
    "DBF_TRUNCATED",
    "FPT_REQUIRED_MISSING",
    "FPT_INVALID",
    "ENCODING_UNKNOWN",
    "TEXT_DECODE_ERROR",
    "FIELD_TYPE_UNSUPPORTED",
    "OPTIONAL_DEPENDENCY_MISSING",
    "OUTPUT_EXISTS",
    "RECONSTRUCTION_FAILED",
    "ROUNDTRIP_MISMATCH",
)

DOCUMENTED_COMPATIBILITY_ALIASES = ("dbf_bridge",)


# ---------------------------------------------------------------------------
# import boundary + symbol parity
# ---------------------------------------------------------------------------


def test_preferred_import_boundary_works() -> None:
    import dbfbridge  # noqa: F401  (explicit preferred boundary check)

    assert dbfbridge.__version__


def test_documented_stable_symbols_are_public() -> None:
    exported = set(dbfbridge.__all__)
    for name in (*STABLE_OPERATIONS, *STABLE_MODELS, *STABLE_ERRORS, *STABLE_POLICIES, *STABLE_PROGRESS):
        assert name in exported, f"undocumented-or-missing stable symbol: {name}"
        assert getattr(dbfbridge, name, None) is not None


def test_compatibility_alias_mirrors_the_preferred_surface() -> None:
    alias = __import__(DOCUMENTED_COMPATIBILITY_ALIASES[0])
    assert set(alias.__all__) == set(dbf_bridge.__all__)
    for name in dbf_bridge.__all__:
        assert getattr(alias, name) is getattr(dbf_bridge, name)


# ---------------------------------------------------------------------------
# the nine stable public operations
# ---------------------------------------------------------------------------


def test_nine_public_operations_exist() -> None:
    for name in STABLE_OPERATIONS:
        operation = getattr(dbfbridge, name)
        assert callable(operation)
        import inspect

        signature = inspect.signature(operation)
        assert list(signature.parameters)[0] in {"path", "source"}


# ---------------------------------------------------------------------------
# RawMode contract
# ---------------------------------------------------------------------------


def test_rawmode_choices_are_the_documented_three() -> None:
    args = typing.get_args(dbfbridge.RawMode)
    assert set(args) == {"none", "metadata", "full-record"}


def test_rawmode_default_is_full_record() -> None:
    import inspect

    signature = inspect.signature(dbfbridge.export_dbf)
    assert signature.parameters["raw_mode"].default == "full-record"
    options = dbfbridge.ExportOptions()
    assert options.raw_mode == "full-record"


# ---------------------------------------------------------------------------
# machine-code vocabulary
# ---------------------------------------------------------------------------


def test_architecture_required_error_codes_exist() -> None:
    codes = {member.value for member in dbfbridge.ErrorCode}
    for required in REQUIRED_ERROR_CODES:
        assert required in codes


def test_machine_code_vocabulary_is_stable_and_closed() -> None:
    codes = sorted(member.value for member in dbfbridge.ErrorCode)
    assert codes == [
        "ARGUMENT_INVALID",
        "DBF_FORMAT_UNSUPPORTED",
        "DBF_HEADER_INVALID",
        "DBF_IO_ERROR",
        "DBF_RECORD_INVALID",
        "DBF_TRUNCATED",
        "ENCODING_UNKNOWN",
        "FIELD_PROJECTION_INVALID",
        "FIELD_TYPE_UNSUPPORTED",
        "FPT_INVALID",
        "FPT_REQUIRED_MISSING",
        "OPERATION_FAILED",
        "OPTIONAL_DEPENDENCY_MISSING",
        "OUTPUT_EXISTS",
        "PATH_NOT_FOUND",
        "READ_CANCELLED",
        "RECONSTRUCTION_FAILED",
        "ROUNDTRIP_MISMATCH",
        "TEXT_DECODE_ERROR",
    ]


# ---------------------------------------------------------------------------
# JSON-safe run-result boundary (real run, §5)
# ---------------------------------------------------------------------------


def test_run_results_expose_the_documented_json_boundary(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    export_run = dbfbridge.export_dbf(
        sample_input_dir, tmp_path / "exported", formats=("jsonl",), overwrite=True
    )
    payload = export_run.to_dict()
    json.dumps(payload)

    reconstruct_run = dbfbridge.reconstruct_dbf(
        tmp_path / "exported", tmp_path / "rebuilt", overwrite=True
    )
    json.dumps(reconstruct_run.to_dict())

    verify_run = dbfbridge.verify_conversion(sample_input_dir, tmp_path / "exported", formats=("jsonl",))
    json.dumps(verify_run.to_dict())

    quality_run = dbfbridge.check_conversion_quality(sample_input_dir, tmp_path / "quality")
    json.dumps(quality_run.to_dict())

    assert export_run.successful and verify_run.successful


# ---------------------------------------------------------------------------
# compatibility aliases + private-module independence
# ---------------------------------------------------------------------------


def test_documented_compatibility_aliases_remain_available() -> None:
    alias_module = __import__(DOCUMENTED_COMPATIBILITY_ALIASES[0])
    assert alias_module.export_dbf is dbfbridge.export_dbf
    assert alias_module.inspect_table is dbfbridge.inspect_table


def test_examples_require_no_private_module_imports() -> None:
    examples_dir = Path(__file__).resolve().parent.parent / "examples"
    private_imports: list[str] = []
    for path in examples_dir.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and (
                "dbf_bridge.core" in stripped
                or "dbf_bridge.exporter" in stripped
                or "dbf_bridge.importer" in stripped
                or "dbfbridge.core" in stripped
                or "dbfbridge.exporter" in stripped
                or "dbfbridge.importer" in stripped
            ):
                private_imports.append(f"{path.name}: {stripped}")
    assert private_imports == []
