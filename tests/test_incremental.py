from __future__ import annotations

import json
import shutil
from pathlib import Path

from dbf_bridge.cli import main
from dbf_bridge.exporter.incremental import CHECKSUM_MANIFEST_NAME
from dbf_bridge.importer.readers import discover_inputs


def _run(source: Path, output: Path, *, incremental: bool = False) -> list[dict[str, object]]:
    args = [
        "--source",
        str(source),
        "--output",
        str(output),
        "--formats",
        "jsonl",
        "--overwrite",
        "--no-progress",
    ]
    if incremental:
        args.append("--incremental")
    assert main(args) == 0
    return [
        json.loads(line)
        for line in (output / "migration_report.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def _statuses(report: list[dict[str, object]]) -> dict[str, object]:
    return {
        str(item["table"]): item["status"]
        for item in report
        if item.get("type") == "table"
    }


def test_incremental_export_reuses_only_complete_unchanged_tables(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures" / "input"
    source = tmp_path / "source"
    output = tmp_path / "output"
    shutil.copytree(fixtures, source)

    first = _run(source, output)
    manifest_path = output / CHECKSUM_MANIFEST_NAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_format"] == "dbfbridge-conversion-checksums"
    assert set(manifest["tables"]) == {
        "archiwum/stare_dane.dbf",
        "klienci.dbf",
        "zamowienia/zamowienia.dbf",
    }
    assert first[0]["skipped"] == 0

    second = _run(source, output, incremental=True)
    assert second[0]["ok"] == 0
    assert second[0]["skipped"] == 3
    assert set(_statuses(second).values()) == {"SKIPPED"}
    assert second[0]["run"]["converted_tables"] == 0
    assert second[0]["run"]["skipped_tables"] == 3

    manifest_path.write_text("not-json", encoding="utf-8")
    recovered = _run(source, output, incremental=True)
    assert recovered[0]["ok"] == 3
    assert recovered[0]["skipped"] == 0
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_version"] == 1

    corrupted = output / "zamowienia" / "zamowienia.jsonl"
    with corrupted.open("ab") as outfile:
        outfile.write(b"corruption")
    repaired = _run(source, output, incremental=True)
    assert _statuses(repaired) == {
        "archiwum/stare_dane.dbf": "SKIPPED",
        "klienci.dbf": "SKIPPED",
        "zamowienia/zamowienia.dbf": "OK",
    }

    with (source / "zamowienia" / "zamowienia.dbf").open("ab") as outfile:
        outfile.write(b"\0")
    dbf_changed = _run(source, output, incremental=True)
    assert _statuses(dbf_changed)["zamowienia/zamowienia.dbf"] == "OK"
    assert dbf_changed[0]["skipped"] == 2

    with (source / "klienci.fpt").open("ab") as outfile:
        outfile.write(b"\0")
    memo_changed = _run(source, output, incremental=True)
    assert _statuses(memo_changed)["klienci.dbf"] == "OK"
    assert memo_changed[0]["skipped"] == 2

    (source / "archiwum" / "stare_dane.cdx").write_bytes(b"index-version-1")
    index_changed = _run(source, output, incremental=True)
    assert _statuses(index_changed)["archiwum/stare_dane.dbf"] == "OK"
    assert index_changed[0]["skipped"] == 2

    new_source = source / "nowe" / "nowa_tabela.dbf"
    new_source.parent.mkdir()
    shutil.copyfile(source / "zamowienia" / "zamowienia.dbf", new_source)
    with_new_table = _run(source, output, incremental=True)
    assert _statuses(with_new_table)["nowe/nowa_tabela.dbf"] == "OK"
    assert with_new_table[0]["tables"] == 4
    assert with_new_table[0]["skipped"] == 3

    assert output / "nowe" / "nowa_tabela.jsonl" in discover_inputs(output, "jsonl")
    assert discover_inputs(output, "json") == []


def test_incremental_export_rebuilds_after_configuration_change(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures" / "input"
    source = tmp_path / "source"
    output = tmp_path / "output"
    shutil.copytree(fixtures, source)
    _run(source, output)

    assert (
        main(
            [
                "--source",
                str(source),
                "--output",
                str(output),
                "--formats",
                "jsonl",
                "--overwrite",
                "--no-progress",
                "--incremental",
                "--strip-spaces",
            ]
        )
        == 0
    )
    report = [
        json.loads(line)
        for line in (output / "migration_report.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert report[0]["ok"] == 3
    assert report[0]["skipped"] == 0


def test_incremental_export_caches_internal_jsonl_for_other_formats(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent / "fixtures" / "input"
    source = tmp_path / "source"
    output = tmp_path / "output"
    shutil.copytree(fixtures, source)
    args = [
        "--source",
        str(source),
        "--output",
        str(output),
        "--formats",
        "csv",
        "--overwrite",
        "--no-progress",
    ]
    assert main(args) == 0
    assert main([*args, "--incremental"]) == 0
    report = [
        json.loads(line)
        for line in (output / "migration_report.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert report[0]["requested_formats"] == ["csv"]
    assert report[0]["outputs"] == 6
    assert report[0]["skipped"] == 6
    assert report[0]["complete_tables"] == 3
    assert {item["format"] for item in report[1:]} == {"csv", "jsonl"}
