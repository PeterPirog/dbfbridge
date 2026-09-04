from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import dbf_bridge
import dbfbridge
from dbfbridge import (
    DBFBridgeRunError,
    ExportOptions,
    ProgressEvent,
    check_conversion_quality,
    export_dbf,
    reconstruct_dbf,
    verify_conversion,
)


def test_distribution_import_exposes_the_documented_api() -> None:
    assert dbfbridge.__version__ == dbf_bridge.__version__
    assert {
        "export_dbf",
        "reconstruct_dbf",
        "verify_conversion",
        "check_conversion_quality",
        "ExportOptions",
        "ReconstructionOptions",
        "ProgressEvent",
        "OutputFormat",
        "InputFormat",
    } <= set(dbfbridge.__all__)


def test_current_stable_contract_does_not_include_direct_write() -> None:
    """Direct Write / `write_table` is next-version RESEARCH and is not part
    of the current stable 1.x public contract: it is not exported from the
    package root. This is a snapshot of the CURRENT contract — if a future
    explicit version decision promotes Direct Write, the contract documents
    and this test change together (it is not a permanent rule)."""
    assert not hasattr(dbfbridge, "write_table")
    assert not hasattr(dbf_bridge, "write_table")
    assert "write_table" not in dbfbridge.__all__
    assert "write_table" not in dbf_bridge.__all__


def test_public_api_runs_the_complete_workflow_silently(
    sample_input_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exported = tmp_path / "exported"
    rebuilt = tmp_path / "rebuilt"
    quality = tmp_path / "quality"
    events: list[ProgressEvent] = []

    export_run = export_dbf(
        sample_input_dir,
        exported,
        formats=("jsonl", "csv"),
        memo="inline",
        progress=events.append,
    )

    assert export_run.exit_code == 0
    assert export_run.ok == 6
    assert export_run.failed == 0
    assert export_run.migration_report_jsonl is not None
    assert export_run.migration_report_jsonl.is_file()
    assert export_run.checksum_manifest is not None
    assert export_run.checksum_manifest.is_file()
    assert {event.operation for event in events} == {"export", "convert"}
    assert capsys.readouterr() == ("", "")

    incremental_run = export_dbf(
        sample_input_dir,
        exported,
        formats="jsonl,csv",
        memo="inline",
        incremental=True,
    )
    assert incremental_run.exit_code == 0
    assert incremental_run.skipped == 6

    verification = verify_conversion(
        sample_input_dir,
        exported,
        formats=("jsonl", "csv"),
    )
    assert verification.exit_code == 0
    assert verification.summary["ok"] == 3
    assert verification.report_path is not None
    assert verification.report_path.is_file()

    reconstruction = reconstruct_dbf(
        exported,
        rebuilt,
        input_format="jsonl",
        overwrite=True,
    )
    assert reconstruction.exit_code == 0
    assert reconstruction.ok == 3
    assert reconstruction.report_path.is_file()

    quality_run = check_conversion_quality(
        sample_input_dir,
        quality,
        overwrite=True,
    )
    assert quality_run.exit_code == 0
    assert quality_run.summary["ok"] == 3
    assert quality_run.report_path.is_file()
    assert capsys.readouterr() == ("", "")


def test_options_objects_and_failure_helpers(
    sample_input_dir: Path,
    tmp_path: Path,
) -> None:
    options = ExportOptions(formats=("jsonl",), memo="inline")
    run = export_dbf(sample_input_dir, tmp_path / "exported", options=options)
    assert run.ok == 3

    broken_source = tmp_path / "broken-source"
    shutil.copytree(sample_input_dir, broken_source)
    (broken_source / "klienci.fpt").unlink()
    failed = export_dbf(broken_source, tmp_path / "broken-output")
    assert failed.failed == 1
    with pytest.raises(DBFBridgeRunError) as error:
        failed.raise_for_errors()
    assert error.value.result is failed


@pytest.mark.parametrize("formats", [(), "xml", ("jsonl", "xml")])
def test_export_rejects_invalid_formats(
    sample_input_dir: Path,
    tmp_path: Path,
    formats: object,
) -> None:
    with pytest.raises(ValueError):
        export_dbf(sample_input_dir, tmp_path / "output", formats=formats)  # type: ignore[arg-type]
