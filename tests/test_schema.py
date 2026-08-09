from __future__ import annotations

import base64
import json
from pathlib import Path

import dbf
import tomllib

from dbf_bridge.exporter.config import make_config
from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.writer import export_table


def _create_vfp_table(path: Path) -> None:
    table = dbf.Table(
        str(path),
        field_specs="ID N(6,0); NAZWA C(60); NOTATKA M",
        dbf_type="vfp",
        codepage=0xC8,
    )
    table.open(mode=dbf.READ_WRITE)
    table.append({"ID": 1, "NAZWA": "Zażółć gęślą", "NOTATKA": "Treść memo"})
    table.close()


def test_vfp_schema_contains_reconstruction_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    _create_vfp_table(source / "DANE.dbf")
    discovered = discover_tables(source)[0]
    config = make_config(source=source, output=output, memo="inline", overwrite=True)

    result = export_table(discovered, config)

    schema_path = output / "DANE_schema.json"
    assert result.status == "OK"
    assert result.schema == "DANE_schema.json"
    assert schema_path.is_file()
    assert not (output / "DANE.schema.jsonl").exists()

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["schema_format"] == "dbfbridge-vfp-table-schema"
    assert schema["dbf"]["format_family"] == "Microsoft Visual FoxPro"
    assert schema["dbf"]["recreation_target"] == "Microsoft Visual FoxPro 9.0 SP2"
    assert schema["dbf"]["version_byte"] == "0x30"
    assert schema["dbf"]["record_count_from_header"] == 1
    assert schema["dbf"]["header_length_bytes"] > 0
    assert schema["dbf"]["record_length_bytes"] > 0
    assert len(schema["dbf"]["header_base64"]) > 0
    assert (
        len(base64.b64decode(schema["dbf"]["header_base64"]))
        == schema["dbf"]["header_length_bytes"]
    )
    assert len(schema["source"]["sha256"]) == 64
    assert schema["text_encoding"]["language_driver_byte"] == "0xc8"
    assert schema["text_encoding"]["declared_or_detected_encoding"] == "cp1250"
    assert schema["text_encoding"]["decode_errors"] == "strict"
    assert schema["text_encoding"]["fallback_order"] == [
        "cp1250",
        "cp852",
        "mazovia",
        "piast",
    ]

    memo = schema["memo"]
    assert memo["path"] == "DANE.fpt"
    assert memo["present"] is True
    assert memo["required"] is True
    assert memo["format"] == "FPT"
    assert memo["file_header_bytes"] == 512
    assert memo["block_size_bytes"] > 0
    assert memo["next_free_block"] > 0
    assert memo["block_header_bytes"] == 8
    assert memo["block_header_byte_order"] == "big-endian"
    assert memo["dbf_pointer_byte_order"] == "little-endian"
    assert memo["text_encoding"] == "cp1250"
    assert memo["field_names"] == ["NOTATKA"]
    assert memo["export_policy"] == "inline"
    assert memo["values_in_data_output"] is True
    assert len(memo["sha256"]) == 64
    assert len(memo["header_base64"]) > 0

    fields = {field["name"]: field for field in schema["fields"]}
    assert fields["NAZWA"]["ordinal"] == 2
    assert fields["NAZWA"]["dbf_type"] == "C"
    assert fields["NAZWA"]["dbf_type_name"] == "Character"
    assert fields["NAZWA"]["length"] == 60
    assert fields["NAZWA"]["address"] > 0
    assert len(fields["NAZWA"]["descriptor_base64"]) > 0
    assert fields["NOTATKA"]["dbf_type"] == "M"
    assert fields["NOTATKA"]["length"] == 4
    assert fields["NOTATKA"]["memo_storage"]["file_format"] == "FPT"
    assert fields["NOTATKA"]["memo_storage"]["pointer_length_bytes"] == 4


def test_xlsxwriter_is_installed_by_default() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert any(dependency.startswith("xlsxwriter>=") for dependency in dependencies)
    assert any(dependency.startswith("openpyxl>=") for dependency in dependencies)
    assert any(dependency.startswith("dbf>=") for dependency in dependencies)
