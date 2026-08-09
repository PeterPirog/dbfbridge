from __future__ import annotations

from pathlib import Path

from .models import DiscoveredTable


def discover_tables(source: Path) -> list[DiscoveredTable]:
    """Return a list of DiscoveredTable objects for *source*.

    *source* may be either:
    - a directory – all ``.dbf`` files inside it (recursively) are discovered;
    - a single DBF file – a list containing one ``DiscoveredTable`` is returned.
    """
    source_root = source.resolve()
    if source_root.is_file() and source_root.suffix.lower() == ".dbf":
        # Single DBF file supplied – construct DiscoveredTable directly
        return [
            DiscoveredTable(
                source_path=source_root,
                relative_path=Path(source_root.name),
                memo_path=find_related_file(source_root, ".fpt"),
                memo_present=find_related_file(source_root, ".fpt") is not None,
            )
        ]
    # Otherwise treat as a directory and walk recursively
    dbf_paths = sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".dbf"
        ),
        key=lambda path: path.relative_to(source_root).as_posix().lower(),
    )
    return [
        DiscoveredTable(
            source_path=path,
            relative_path=path.relative_to(source_root),
            memo_path=find_related_file(path, ".fpt"),
            memo_present=find_related_file(path, ".fpt") is not None,
        )
        for path in dbf_paths
    ]



def find_related_file(dbf_path: Path, extension: str) -> Path | None:
    wanted = extension.lower()
    for candidate in dbf_path.parent.iterdir():
        if (
            candidate.is_file()
            and candidate.stem.lower() == dbf_path.stem.lower()
            and candidate.suffix.lower() == wanted
        ):
            return candidate
    return None


def output_data_path(output_root: Path, relative_dbf: Path, export_format: str) -> Path:
    return output_root / relative_dbf.with_suffix(f".{export_format}")


def output_schema_path(output_root: Path, relative_dbf: Path) -> Path:
    return output_root / relative_dbf.with_name(f"{relative_dbf.stem}_schema.json")
