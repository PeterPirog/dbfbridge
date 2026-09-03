"""Machine-readable public error contract (architecture §17, closure BLK-01).

Every test in this module classifies failures from structured payloads only —
``exc.code``, ``exc.to_dict()``, ``result.error_details`` or
``result.to_dict()``.  No test may match English message substrings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import vfp_fixture_factory as factory

from dbf_bridge import (
    DBFBridgeRunError,
    ErrorCode,
    OperationArgumentError,
    OperationOutputExistsError,
    OperationPathError,
    OptionalDependencyMissingError,
    check_conversion_quality,
    export_dbf,
    reconstruct_dbf,
    verify_conversion,
)
from dbf_bridge.exporter.writer import ensure_can_write_final

#: Every public-boundary structured exception (all carry ``code``/``to_dict``).
PUBLIC_OPERATION_ERRORS = (
    OperationArgumentError,
    OperationPathError,
    OperationOutputExistsError,
)

# ---------------------------------------------------------------------------
# helpers (message-blind classification)
# ---------------------------------------------------------------------------


def _detail_codes(result: object) -> list[str]:
    details = getattr(result, "error_details", [])
    return [detail.code for detail in details]


def _first_code(result: object) -> str:
    codes = _detail_codes(result)
    assert codes, "expected at least one structured error detail"
    return codes[0]


# ---------------------------------------------------------------------------
# argument errors (ARGUMENT_INVALID, ValueError-compatible)
# ---------------------------------------------------------------------------


def test_argument_errors_are_typed_and_remain_value_errors(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(OperationArgumentError) as memo_error:
        export_dbf(sample_input_dir, tmp_path / "out", memo="bogus")
    assert memo_error.value.code == "ARGUMENT_INVALID"
    assert isinstance(memo_error.value, ValueError)
    assert json.dumps(memo_error.value.to_dict())

    with pytest.raises(OperationArgumentError) as raw_error:
        export_dbf(sample_input_dir, tmp_path / "out", raw_mode="bogus")
    assert raw_error.value.code == "ARGUMENT_INVALID"

    with pytest.raises(OperationArgumentError) as quality_error:
        check_conversion_quality(sample_input_dir, tmp_path / "quality", max_differences=0)
    assert quality_error.value.code == "ARGUMENT_INVALID"
    assert isinstance(quality_error.value, ValueError)

    with pytest.raises(OperationArgumentError) as format_error:
        reconstruct_dbf(sample_input_dir, tmp_path / "rebuilt", input_format="xml")
    assert format_error.value.code == "ARGUMENT_INVALID"


def test_error_messages_are_unchanged_for_arguments(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(OperationArgumentError) as error:
        export_dbf(sample_input_dir, tmp_path / "out", memo="bogus")
    assert str(error.value) == "memo must be one of: skip, inline, null"


def test_options_guard_error_is_typed(sample_input_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(OperationArgumentError) as error:
        export_dbf(
            sample_input_dir,
            tmp_path / "out",
            formats=("jsonl",),
            options=None,  # type: ignore[arg-type]
            memo="bogus",
        )
    assert error.value.code == "ARGUMENT_INVALID"


# ---------------------------------------------------------------------------
# path errors (PATH_NOT_FOUND, FileNotFoundError-compatible)
# ---------------------------------------------------------------------------


def test_path_errors_are_typed_and_remain_file_not_found(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.dbf"
    with pytest.raises(OperationPathError) as export_error:
        export_dbf(missing, tmp_path / "out")
    assert export_error.value.code == "PATH_NOT_FOUND"
    assert isinstance(export_error.value, FileNotFoundError)
    assert json.dumps(export_error.value.to_dict())
    assert export_error.value.to_dict()["path"] == missing.as_posix()

    with pytest.raises(OperationPathError) as verify_error:
        verify_conversion(tmp_path / "no-source", tmp_path / "no-output")
    assert verify_error.value.code == "PATH_NOT_FOUND"

    with pytest.raises(OperationPathError) as quality_error:
        check_conversion_quality(tmp_path / "no-source", tmp_path / "quality")
    assert quality_error.value.code == "PATH_NOT_FOUND"


# ---------------------------------------------------------------------------
# output conflict (OUTPUT_EXISTS, FileExistsError-compatible)
# ---------------------------------------------------------------------------


def test_output_exists_is_machine_readable_per_table(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    output = tmp_path / "exported"
    first = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=True)
    assert first.ok == 3

    conflict = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=False)
    assert conflict.failed == 3
    for result in conflict.results:
        assert result.status == "FAILED"
        assert _first_code(result) == "OUTPUT_EXISTS"
        assert result.errors, "human-readable error text is preserved"

    with pytest.raises(DBFBridgeRunError) as run_error:
        conflict.raise_for_errors()
    payload = run_error.value.to_dict()
    assert json.dumps(payload)
    assert payload["code"] == "OUTPUT_EXISTS"
    assert {detail["code"] for detail in payload["details"]} == {"OUTPUT_EXISTS"}
    assert isinstance(run_error.value, RuntimeError)


def test_ensure_can_write_final_keeps_file_exists_error_compatible(tmp_path: Path) -> None:
    existing = tmp_path / "data.jsonl"
    existing.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ensure_can_write_final(existing, overwrite=False)
    try:
        ensure_can_write_final(existing, overwrite=False)
    except FileExistsError as exc:
        assert getattr(exc, "code", None) == "OUTPUT_EXISTS"
        assert json.dumps(exc.to_dict())
    else:  # pragma: no cover
        pytest.fail("expected OutputExistsError")


# ---------------------------------------------------------------------------
# per-table structured details (export)
# ---------------------------------------------------------------------------


def test_unsupported_table_reports_field_type_unsupported(tmp_path: Path) -> None:
    source = factory.build_vfp32_table(
        tmp_path / "varbin.dbf",
        columns=[
            {"name": "K", "type": "N", "width": 4},
            {"name": "BIN", "type": "Q", "width": 10},
        ],
        rows=[{"K": 1, "BIN": b"\x00\x01payload"}],
    )
    run = export_dbf(source, tmp_path / "exported", formats=("jsonl",), overwrite=True)
    assert run.failed == 1
    result = run.results[0]
    assert result.status == "UNSUPPORTED"
    assert _first_code(result) == "FIELD_TYPE_UNSUPPORTED"
    assert json.dumps(result.to_report_dict())


def test_missing_fpt_reports_fpt_required_missing(tmp_path: Path) -> None:
    import dbf

    source = tmp_path / "source"
    source.mkdir()
    dbf_path = source / "memo.dbf"
    table = dbf.Table(str(dbf_path), "NOTATKA M", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    table.append({"NOTATKA": "znotatka"})
    table.close()
    (source / "memo.fpt").unlink()

    run = export_dbf(dbf_path, tmp_path / "exported", formats=("jsonl",), overwrite=True)
    assert run.failed == 1
    assert _first_code(run.results[0]) == "FPT_REQUIRED_MISSING"


def test_decode_failure_keeps_text_decode_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typed core decode failure must not be flattened to a generic export
    failure — the structured detail keeps ``TEXT_DECODE_ERROR``.

    The loss-aware Polish fallback chain makes a real undecodable byte
    unreachable at export time (every high byte decodes via cp1250/cp852/
    mazovia), so the typed failure is simulated at the shared backend seam.
    """
    import dbf

    from dbf_bridge.core.backend import dbfread_backend
    from dbf_bridge.core.errors import TextDecodeError

    source = tmp_path / "source"
    source.mkdir()
    dbf_path = source / "broken.dbf"
    table = dbf.Table(str(dbf_path), "TEXT C(4)", dbf_type="vfp", codepage=0xC8)
    table.open(dbf.READ_WRITE)
    table.append({"TEXT": "x"})
    table.close()

    def _raise_typed_decode_error(*args: object, **kwargs: object) -> object:
        raise TextDecodeError(
            "value cannot be decoded",
            path=dbf_path,
            context={"field": "TEXT"},
        )

    monkeypatch.setattr(
        dbfread_backend, "iter_physical_records", _raise_typed_decode_error
    )
    run = export_dbf(dbf_path, tmp_path / "exported", formats=("jsonl",), overwrite=True)
    assert run.failed == 1
    result = run.results[0]
    assert result.status == "FAILED"
    assert _first_code(result) == "TEXT_DECODE_ERROR"


# ---------------------------------------------------------------------------
# per-table structured details (reconstruction)
# ---------------------------------------------------------------------------


def _export_tree(source: Path, output: Path) -> None:
    run = export_dbf(source, output, formats=("jsonl",), overwrite=True)
    assert run.ok == 3


def _jsonl_lines(output: Path, table: str) -> list[dict[str, object]]:
    path = output / "zamowienia" / "zamowienia.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_roundtrip_mismatch_is_machine_readable(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    exported = tmp_path / "exported"
    _export_tree(sample_input_dir, exported)

    data_file = exported / "zamowienia" / "zamowienia.jsonl"
    lines = [json.loads(line) for line in data_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) >= 2
    lines[0]["STATUS"], lines[1]["STATUS"] = lines[1]["STATUS"], lines[0]["STATUS"]
    data_file.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )

    run = reconstruct_dbf(exported, tmp_path / "rebuilt", overwrite=True)
    assert run.failed >= 1
    mismatched = [
        result
        for result in run.results
        if result.source == "zamowienia/zamowienia.jsonl"
    ]
    assert mismatched and mismatched[0].canonical_match is False
    assert "ROUNDTRIP_MISMATCH" in _detail_codes(mismatched[0])
    assert json.dumps(run.to_dict())


def test_reconstruction_failure_is_machine_readable(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    exported = tmp_path / "exported"
    _export_tree(sample_input_dir, exported)

    data_file = exported / "zamowienia" / "zamowienia.jsonl"
    lines = [json.loads(line) for line in data_file.read_text(encoding="utf-8").splitlines() if line]
    lines[0]["KWOTA"] = "not-a-number"
    data_file.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )

    run = reconstruct_dbf(exported, tmp_path / "rebuilt", overwrite=True)
    assert run.failed >= 1
    failed = [result for result in run.results if result.status == "FAILED"]
    assert failed
    assert _first_code(failed[0]) == "RECONSTRUCTION_FAILED"
    assert json.dumps(run.to_dict())


def test_output_exists_in_reconstruction_is_machine_readable(
    sample_input_dir: Path, tmp_path: Path
) -> None:
    exported = tmp_path / "exported"
    _export_tree(sample_input_dir, exported)
    rebuilt = tmp_path / "rebuilt"
    assert reconstruct_dbf(exported, rebuilt, overwrite=True).ok == 3

    conflict = reconstruct_dbf(exported, rebuilt, overwrite=False)
    assert conflict.failed == 3
    for result in conflict.results:
        assert _first_code(result) == "OUTPUT_EXISTS"


# ---------------------------------------------------------------------------
# run-level JSON-safe serialization + run error payload
# ---------------------------------------------------------------------------


def test_run_level_results_are_json_safe(sample_input_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "exported"
    export_run = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=True)
    assert json.dumps(export_run.to_dict())

    reconstruct_run = reconstruct_dbf(output, tmp_path / "rebuilt", overwrite=True)
    assert json.dumps(reconstruct_run.to_dict())

    verify_run = verify_conversion(sample_input_dir, output, formats=("jsonl",))
    assert json.dumps(verify_run.to_dict())

    quality_run = check_conversion_quality(sample_input_dir, tmp_path / "quality")
    assert json.dumps(quality_run.to_dict())


def test_run_error_payload_preserves_all_details(sample_input_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "exported"
    assert export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=True).ok == 3
    conflict = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=False)
    with pytest.raises(DBFBridgeRunError) as error:
        conflict.raise_for_errors()
    payload = error.value.to_dict()
    assert len(payload["details"]) == 3
    assert all(detail["code"] == "OUTPUT_EXISTS" for detail in payload["details"])
    assert {detail["table"] for detail in payload["details"]} == {
        "archiwum/stare_dane.dbf",
        "klienci.dbf",
        "zamowienia/zamowienia.dbf",
    }
    assert error.value.code == "OUTPUT_EXISTS"
    assert isinstance(error.value, RuntimeError)


# ---------------------------------------------------------------------------
# the acceptance test: MCP classification without parsing English text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("invalid-argument", "ARGUMENT_INVALID"),
        ("missing-path", "PATH_NOT_FOUND"),
        ("output-exists", "OUTPUT_EXISTS"),
        ("unsupported-table", "FIELD_TYPE_UNSUPPORTED"),
        ("reconstruction-failure", "RECONSTRUCTION_FAILED"),
        ("roundtrip-mismatch", "ROUNDTRIP_MISMATCH"),
    ],
)
def test_mcp_machine_readable_classification(
    sample_input_dir: Path,
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    """A consumer classifies failures ONLY via ``code``/``to_dict`` payloads."""
    output = tmp_path / "exported"
    codes: set[str] = set()
    if case == "invalid-argument":
        try:
            export_dbf(sample_input_dir, tmp_path / "x", memo="bogus")
        except PUBLIC_OPERATION_ERRORS as exc:
            codes = {exc.code}
    elif case == "missing-path":
        try:
            export_dbf(tmp_path / "missing.dbf", tmp_path / "x")
        except PUBLIC_OPERATION_ERRORS as exc:
            codes = {exc.code}
    elif case == "output-exists":
        assert export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=True).ok == 3
        conflict = export_dbf(sample_input_dir, output, formats=("jsonl",), overwrite=False)
        codes = set(_detail_codes(conflict.results[0]))
    elif case == "unsupported-table":
        source = factory.build_vfp32_table(
            tmp_path / "varbin.dbf",
            columns=[{"name": "BIN", "type": "Q", "width": 10}],
            rows=[{"BIN": b"payload"}],
        )
        run = export_dbf(source, tmp_path / "u", formats=("jsonl",), overwrite=True)
        codes = set(_detail_codes(run.results[0]))
    elif case == "reconstruction-failure":
        _export_tree(sample_input_dir, output)
        data_file = output / "zamowienia" / "zamowienia.jsonl"
        lines = [
            json.loads(line)
            for line in data_file.read_text(encoding="utf-8").splitlines()
            if line
        ]
        lines[0]["KWOTA"] = "not-a-number"
        data_file.write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
            encoding="utf-8",
        )
        run = reconstruct_dbf(output, tmp_path / "rebuilt", overwrite=True)
        failed = [result for result in run.results if result.status == "FAILED"]
        codes = set(_detail_codes(failed[0]))
    elif case == "roundtrip-mismatch":
        _export_tree(sample_input_dir, output)
        data_file = output / "zamowienia" / "zamowienia.jsonl"
        lines = [
            json.loads(line)
            for line in data_file.read_text(encoding="utf-8").splitlines()
            if line
        ]
        lines[0]["STATUS"], lines[1]["STATUS"] = lines[1]["STATUS"], lines[0]["STATUS"]
        data_file.write_text(
            "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
            encoding="utf-8",
        )
        run = reconstruct_dbf(output, tmp_path / "rebuilt2", overwrite=True)
        mismatched = [result for result in run.results if result.canonical_match is False]
        assert mismatched
        codes = set(_detail_codes(mismatched[0]))
    assert codes == {expected_code}, codes


def test_optional_dependency_code_is_canonical() -> None:
    assert OptionalDependencyMissingError.code == ErrorCode.OPTIONAL_DEPENDENCY_MISSING.value


# ---------------------------------------------------------------------------
# frozen-area guard: Direct Read error payloads unchanged
# ---------------------------------------------------------------------------


def test_direct_read_error_payload_shape_is_unchanged() -> None:
    from dbf_bridge.core.errors import DbfHeaderInvalidError

    error = DbfHeaderInvalidError(
        "boom",
        path=Path("t.dbf"),
        context={"offset": 3, "blob": b"\x00\x01"},
    )
    payload = error.to_dict()
    assert set(payload) == {"code", "message", "path", "context"}
    assert json.dumps(payload)
    assert isinstance(error, ValueError)
