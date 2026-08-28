"""Deterministic DBF/FPT fixture generators for the Phase 0 benchmark runner.

Every generator is seeded and produces stable content for a given record
count.  The fixtures live outside the repository (``benchmark-data``) and are
never committed.  Text uses only characters that decode identically under
cp1250, cp852 and Mazovia, so encoding scenarios compare code paths, not data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

TEXT = "zaolc gescia jazn 123"
MEMO_TEXT = "Mazowska tresc benchmarkowa. Linia z przecinkami, cudzyslown: 'cytat'. "
POLISH_SAFE = [
    "Aldonka",
    "Bozena",
    "Czeslaw",
    "Dorota",
    "Ewa",
    "Franciszek",
    "Grazyna",
    "Henryk",
    "Irena",
    "Janusz",
    "Katarzyna",
    "Lucyna",
    "Marek",
    "Natalia",
    "Oskar",
    "Piotr",
    "Rafal",
    "Slawomir",
    "Tomasz",
    "Urszula",
]


def _spec(name: str, type_: str, length: int, decimal: int) -> str:
    upper = type_.upper()
    if upper in {"M", "L", "D", "T", "Y"}:
        return f"{name} {type_}"
    if upper == "C":
        return f"{name} {type_}({length})"
    return f"{name} {type_}({length},{decimal})"


def _create(path: Path, specs: list[tuple[str, str, int, int]]) -> Any:
    import dbf

    path.parent.mkdir(parents=True, exist_ok=True)
    for existing in (path, path.with_suffix(".fpt")):
        if existing.exists():
            existing.unlink()
    table = dbf.Table(
        str(path),
        field_specs="; ".join(_spec(*spec) for spec in specs),
        dbf_type="vfp",
        codepage=0xC8,  # cp1250 language driver; encoding scenarios force a codec
    )
    table.open(mode=dbf.READ_WRITE)
    return table


def generate_flat(
    path: Path,
    records: int,
    *,
    deleted_fraction: float = 0.0,
) -> dict[str, object]:
    """Wide, memo-free table exercising C/N/L/D/T/Y field parsing."""

    table = _create(
        path,
        [
            ("ID", "N", 12, 0),
            ("NAZWA", "C", 40, 0),
            ("MIASTO", "C", 24, 0),
            ("KWOTA", "N", 12, 2),
            ("LICZBA", "Y", 8, 0),
            ("AKTYWNY", "L", 1, 0),
            ("DATA", "D", 8, 0),
            ("DATA_CZAS", "T", 8, 0),
        ],
    )
    import dbf as dbf_module

    start = date(2020, 1, 1)
    base_dt = datetime(2020, 1, 1, 8, 30, 15)
    deleted = 0
    for i in range(1, records + 1):
        table.append(
            {
                "ID": i,
                "NAZWA": f"{POLISH_SAFE[i % 20]} {TEXT[: (i % 13) + 1]}",
                "MIASTO": POLISH_SAFE[(i * 7) % 20],
                "KWOTA": (i * 37) % 100000 / 100,
                "LICZBA": (i % 997) / 7,
                "AKTYWNY": i % 3 != 0,
                "DATA": date.fromordinal(start.toordinal() + (i % 1500)),
                "DATA_CZAS": None if i % 11 == 0 else base_dt + timedelta(minutes=i),
            }
        )
        if (
            deleted_fraction
            and i % 10 == 0
            and (i // 10) <= max(1, int(records * deleted_fraction))
        ):
            dbf_module.delete(table[-1])
            deleted += 1
    table.close()
    memo = path.with_suffix(".fpt")
    return {
        "table": path.name,
        "records": records,
        "deleted": deleted,
        "dbf_bytes": path.stat().st_size,
        "fpt_bytes": memo.stat().st_size if memo.exists() else 0,
    }


def generate_memo_heavy(
    path: Path,
    records: int,
    *,
    memo_chars: int = 4000,
) -> dict[str, object]:
    """Table where most of the payload lives in FPT memo blocks."""

    table = _create(
        path,
        [
            ("ID", "N", 10, 0),
            ("NAZWA", "C", 30, 0),
            ("NOTATKA", "M", 0, 0),
        ],
    )
    memo_body = (MEMO_TEXT * (memo_chars // len(MEMO_TEXT) + 1))[:memo_chars]
    for i in range(1, records + 1):
        table.append(
            {
                "ID": i,
                "NAZWA": f"{POLISH_SAFE[i % 20]} {i}",
                "NOTATKA": None if i % 10 == 0 else memo_body,
            }
        )
    table.close()
    memo = path.with_suffix(".fpt")
    return {
        "table": path.name,
        "records": records,
        "deleted": 0,
        "dbf_bytes": path.stat().st_size,
        "fpt_bytes": memo.stat().st_size if memo.exists() else 0,
    }


def fixture_manifest(fixture_dir: Path) -> dict[str, object]:
    manifest: dict[str, object] = {"directory": str(fixture_dir)}
    if fixture_dir.is_dir():
        for path in sorted(fixture_dir.rglob("*")):
            if path.is_file():
                manifest[path.as_posix()] = path.stat().st_size
    return manifest
