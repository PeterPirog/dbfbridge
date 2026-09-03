"""Mechanical 1.0 public API contract regression tests (docs/api-1.0.md).

These tests freeze PUBLIC behaviour only — the preferred import boundary and
alias parity, the stable symbol surface, the nine public operations with
their 1.0 signature baselines, the RawMode choices, the architecture-required
machine codes and the protected 1.0 stability baseline (a subset, not a
closed vocabulary), the per-family structured error payloads, and the
JSON-safe run-result boundary.  They deliberately avoid snapshotting
implementation internals so harmless internal refactoring stays possible, and
they leave room for the documented backward-compatible evolution (additive
machine codes, additive keyword-only parameters, additive JSON keys).
"""

from __future__ import annotations

import inspect
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


def test_alias_parity_between_preferred_and_historical_names() -> None:
    """`dbfbridge` (preferred) vs `dbf_bridge` (compatibility alias) parity."""
    alias = __import__(DOCUMENTED_COMPATIBILITY_ALIASES[0])
    assert set(dbfbridge.__all__) == set(alias.__all__)
    for name in dbfbridge.__all__:
        preferred = getattr(dbfbridge, name)
        historical = getattr(alias, name)
        if name == "__version__":
            assert preferred == historical
        else:
            assert preferred is historical


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
# signature compatibility baseline (docs/api-1.0.md §2)
# ---------------------------------------------------------------------------

# The 1.0 compatibility baseline: existing parameter names, their kinds, and
# documented defaults. Extra OPTIONAL keyword-only parameters with defaults
# may appear in a MINOR release; nothing in the baseline may change.
OPERATION_BASELINE: dict[str, tuple[tuple[str, str, object], ...]] = {
    "inspect_table": (("path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),),
    "read_schema": (("path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),),
    "iter_records": (
        ("path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("fields", "KEYWORD_ONLY", None),
        ("include_deleted", "KEYWORD_ONLY", False),
        ("memo", "KEYWORD_ONLY", "lazy"),
        ("raw", "KEYWORD_ONLY", False),
        ("encoding", "KEYWORD_ONLY", "auto"),
        ("decode_errors", "KEYWORD_ONLY", "strict"),
        ("progress", "KEYWORD_ONLY", None),
        ("cancel_check", "KEYWORD_ONLY", None),
    ),
    "read_records": (
        ("path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("offset", "KEYWORD_ONLY", 0),
        ("limit", "KEYWORD_ONLY", 100),
        ("fields", "KEYWORD_ONLY", None),
        ("include_deleted", "KEYWORD_ONLY", False),
        ("memo", "KEYWORD_ONLY", "lazy"),
        ("raw", "KEYWORD_ONLY", False),
        ("encoding", "KEYWORD_ONLY", "auto"),
        ("decode_errors", "KEYWORD_ONLY", "strict"),
        ("progress", "KEYWORD_ONLY", None),
        ("cancel_check", "KEYWORD_ONLY", None),
    ),
    "iter_raw_records": (
        ("path", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("progress", "KEYWORD_ONLY", None),
        ("cancel_check", "KEYWORD_ONLY", None),
    ),
    "export_dbf": (
        ("source", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("output", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("formats", "KEYWORD_ONLY", None),
        ("memo", "KEYWORD_ONLY", None),
        ("strip_spaces", "KEYWORD_ONLY", False),
        ("encoding", "KEYWORD_ONLY", "auto"),
        ("decode_errors", "KEYWORD_ONLY", "strict"),
        ("deleted", "KEYWORD_ONLY", "skip"),
        ("missing_memo", "KEYWORD_ONLY", "fail"),
        ("overwrite", "KEYWORD_ONLY", True),
        ("validate", "KEYWORD_ONLY", True),
        ("xlsx_long_text", "KEYWORD_ONLY", "overflow"),
        ("incremental", "KEYWORD_ONLY", False),
        ("raw_mode", "KEYWORD_ONLY", "full-record"),
        ("progress", "KEYWORD_ONLY", None),
        ("options", "KEYWORD_ONLY", None),
    ),
    "reconstruct_dbf": (
        ("source", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("output", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("input_format", "KEYWORD_ONLY", "jsonl"),
        ("memo", "KEYWORD_ONLY", "inline"),
        ("overwrite", "KEYWORD_ONLY", False),
        ("progress", "KEYWORD_ONLY", None),
        ("options", "KEYWORD_ONLY", None),
    ),
    "verify_conversion": (
        ("source", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("output", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("formats", "KEYWORD_ONLY", ("csv", "json", "jsonl", "xlsx")),
        ("strict", "KEYWORD_ONLY", True),
        ("report", "KEYWORD_ONLY", None),
        ("write_report", "KEYWORD_ONLY", True),
        ("verbose", "KEYWORD_ONLY", False),
    ),
    "check_conversion_quality": (
        ("source", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("output", "POSITIONAL_OR_KEYWORD", inspect.Parameter.empty),
        ("overwrite", "KEYWORD_ONLY", False),
        ("max_differences", "KEYWORD_ONLY", 20),
        ("progress", "KEYWORD_ONLY", None),
    ),
}


def test_operation_signatures_keep_the_1_0_baseline() -> None:
    """Existing parameter names, kinds and documented defaults are protected;
    future additive keyword-only parameters remain allowed (MINOR release)."""
    import inspect

    for name, baseline in OPERATION_BASELINE.items():
        signature = inspect.signature(getattr(dbfbridge, name))
        parameters = signature.parameters
        for param_name, kind, default in baseline:
            parameter = parameters.get(param_name)
            assert parameter is not None, f"{name}: baseline parameter {param_name} removed"
            assert parameter.kind.name == kind, f"{name}.{param_name}: kind changed"
            if default is inspect.Parameter.empty:
                assert parameter.default is inspect.Parameter.empty, (
                    f"{name}.{param_name}: was required, now has a default"
                )
            else:
                assert parameter.default == default, f"{name}.{param_name}: default changed"
        extras = set(parameters) - {entry[0] for entry in baseline}
        for extra in sorted(extras):
            parameter = parameters[extra]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{name}.{extra}: new parameters must be keyword-only"
            )
            assert parameter.default is not inspect.Parameter.empty, (
                f"{name}.{extra}: new parameters must be optional"
            )


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


STABLE_1_0_ERROR_CODES = frozenset(
    {
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
    }
)


def test_stable_1_0_error_codes_remain_available() -> None:
    """The 1.0 stability baseline is a PROTECTED SUBSET, not a closed set.

    Every code existing at the 1.0 declaration must stay available with its
    exact string; a later 1.x MINOR release may still ADD a new machine code
    (docs/api-1.0.md §4).  Removing/renaming/repurposing a baseline code is a
    breaking MAJOR change.
    """
    current = {member.value for member in dbfbridge.ErrorCode}
    assert current >= STABLE_1_0_ERROR_CODES


# ---------------------------------------------------------------------------
# error payload families (docs/api-1.0.md §4 — verified against the runtime)
# ---------------------------------------------------------------------------


def test_direct_read_error_payload_family() -> None:
    from dbf_bridge import DbfTruncatedError

    error = DbfTruncatedError("boom", path=Path("t.dbf"), context={"a": 1})
    payload = error.to_dict()
    required = {"code", "message", "path", "context"}
    assert required <= set(payload)
    assert payload["code"] == "DBF_TRUNCATED"
    json.dumps(payload)


def test_operation_error_payload_family() -> None:
    error = dbfbridge.OperationArgumentError(
        "bad argument", operation="export_dbf", context={"k": "v"}
    )
    payload = error.to_dict()
    required = {"code", "message", "operation", "path", "table", "context"}
    assert required <= set(payload)
    assert payload["code"] == "ARGUMENT_INVALID"
    json.dumps(payload)


def test_optional_dependency_payload_family() -> None:
    error = dbfbridge.OptionalDependencyMissingError(
        dependency="xlsxwriter",
        extra="xlsx",
        operation="export_dbf",
        purpose="XLSX conversion",
    )
    payload = error.to_dict()
    required = {"code", "dependency", "extra", "operation", "install_command"}
    assert required <= set(payload)
    assert payload["purpose"] == "XLSX conversion"
    assert error.code == "OPTIONAL_DEPENDENCY_MISSING"
    json.dumps(payload)


def test_dbf_bridge_run_error_aggregate_payload() -> None:
    detail = dbf_bridge.OperationError(
        code="OUTPUT_EXISTS",
        message="refusing to overwrite",
        operation="export_dbf",
        table="klienci.dbf",
    )
    error = dbfbridge.DBFBridgeRunError("run failed", None, details=(detail,))
    payload = error.to_dict()
    assert {"code", "message", "details"} <= set(payload)
    assert payload["code"] == "OUTPUT_EXISTS"  # primary detail's code
    assert payload["details"] == [detail.to_dict()]
    assert error.code == "OUTPUT_EXISTS"
    assert isinstance(error, RuntimeError)
    json.dumps(payload)


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
