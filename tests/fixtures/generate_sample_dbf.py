"""
generate_sample_dbf.py
======================

Tworzy syntetyczną strukturę katalogów z przykładowymi plikami DBF
(wraz z plikami memo FPT) umożliwiającą uruchomienie
05_dbf_tree_to_csv.py.

Wygenerowane dane obejmują różne typy pól DBF:
- C (Character)     – tekst
- N (Numeric)       – liczby całkowite i zmiennoprzecinkowe
- L (Logical)       – wartości logiczne
- D (Date)          – daty
- T (DateTime)      – daty z czasem
- M (Memo)          – teksty długie zapisywane w pliku FPT

Struktura katalogów:
    synthetic_data/input/
    ├── klienci.dbf + klienci.fpt
    ├── zamowienia/
    │   └── zamowienia.dbf
    └── archiwum/
        └── stare_dane.dbf + stare_dane.fpt

Użycie:
    python generate_sample_dbf.py
"""

from __future__ import annotations

import random
import string
from datetime import date, datetime, timedelta
from pathlib import Path

import dbf


def random_text(length: int) -> str:
    """Generuje losowy ciąg znaków ASCII."""
    return "".join(random.choices(string.ascii_letters + string.digits + " _-", k=length))


def build_spec(name: str, type_: str, length: int, decimal: int) -> str:
    """Buduje pojedynczy spec pola DBF w formacie akceptowanym przez dbf==0.99.11."""
    upper = type_.upper()
    if upper in {"M", "L"}:
        return f"{name} {type_}"
    if upper in {"D", "T"}:
        return f"{name} {type_}"
    if upper == "C":
        return f"{name} {type_}({length})"
    # Numeric: zawsze podaj precyzję, aby biblioteka poprawnie rozpoznała typ.
    return f"{name} {type_}({length},{decimal})"


def create_table(path: Path, fieldspecs: list[tuple[str, str, int, int]]) -> dbf.Table:
    """Tworzy nową tabelę DBF (VFP) z podanym schematem pól."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    # Usuń również towarzyszący plik memo/FPT, jeśli istnieje.
    memo_path = path.with_suffix(".fpt")
    if memo_path.exists():
        memo_path.unlink()
    specs = "; ".join(build_spec(*spec) for spec in fieldspecs)
    table = dbf.Table(
        str(path),
        field_specs=specs,
        dbf_type="vfp",
        codepage=0xC8,  # Windows-1250 (Eastern European / Polish)
    )
    table.open(mode=dbf.READ_WRITE)
    return table


def generate_klienci(base_dir: Path) -> None:
    table = create_table(
        base_dir / "klienci.dbf",
        [
            ("ID_KL", "N", 6, 0),
            ("NAZWA", "C", 60, 0),
            ("EMAIL", "C", 80, 0),
            ("VIP", "L", 1, 0),
            ("NOTATKA", "M", 0, 0),
        ],
    )
    for i in range(1, 21):
        table.append(
            {
                "ID_KL": i,
                "NAZWA": f"Firma {random_text(12).strip()}",
                "EMAIL": f"kontakt{i}@example.com",
                "VIP": random.choice([True, False, None]),
                "NOTATKA": (
                    f"Klient nr {i}. {random_text(80)}\n"
                    f"Dodatkowa linia z przecinkami, cudzysłowami: 'cytat', \"tekst\"."
                    if i % 3 == 0
                    else f"Krótka notatka {i}."
                ),
            }
        )
    table.close()
    print(f"Utworzono: {base_dir / 'klienci.dbf'}")


def generate_zamowienia(base_dir: Path) -> None:
    table = create_table(
        base_dir / "zamowienia" / "zamowienia.dbf",
        [
            ("ID_ZAM", "N", 8, 0),
            ("ID_KL", "N", 6, 0),
            ("DATA_ZAM", "D", 8, 0),
            ("KWOTA", "N", 12, 2),
            ("STATUS", "C", 20, 0),
        ],
    )
    start_date = date(2024, 1, 1)
    statuses = ["nowe", "w realizacji", "wysłane", "zakończone", "anulowane"]
    for i in range(1, 51):
        order_date = start_date + timedelta(days=random.randint(0, 365))
        table.append(
            {
                "ID_ZAM": 10000 + i,
                "ID_KL": random.randint(1, 20),
                "DATA_ZAM": order_date,
                "KWOTA": round(random.uniform(10.0, 5000.0), 2),
                "STATUS": random.choice(statuses),
            }
        )
    table.close()
    print(f"Utworzono: {base_dir / 'zamowienia' / 'zamowienia.dbf'}")


def generate_archiwum(base_dir: Path) -> None:
    table = create_table(
        base_dir / "archiwum" / "stare_dane.dbf",
        [
            ("ID", "N", 5, 0),
            ("NAZWA", "C", 40, 0),
            ("WARTOSC", "N", 15, 4),
            ("AKTYWNY", "L", 1, 0),
            ("DATA_CZAS", "T", 8, 0),
            ("OPIS", "M", 0, 0),
        ],
    )
    base_dt = datetime(2023, 6, 1, 8, 0, 0)
    for i in range(1, 11):
        table.append(
            {
                "ID": i,
                "NAZWA": f"Rekord archiwalny {random_text(15).strip()}",
                "WARTOSC": round(random.uniform(-9999.9999, 9999.9999), 4),
                "AKTYWNY": random.choice([True, False]),
                "DATA_CZAS": base_dt + timedelta(hours=i * 7),
                "OPIS": (
                    "Długi opis archiwalny z wieloma wierszami:\n"
                    "- pierwszy punkt,\n"
                    "- drugi punkt;\n"
                    'zawiera przecinki i "cudzysłowy".'
                    if i % 2 == 0
                    else None
                ),
            }
        )
    table.close()
    print(f"Utworzono: {base_dir / 'archiwum' / 'stare_dane.dbf'}")


def main() -> None:
    random.seed(42)
    base_dir = Path(__file__).resolve().parent / "input"

    if base_dir.exists():
        for item in base_dir.rglob("*"):
            if item.is_file():
                item.unlink()
        for item in sorted(base_dir.rglob("*"), key=lambda p: -len(p.parts)):
            if item.is_dir():
                item.rmdir()
        print(f"Wyczyszczono: {base_dir}")

    generate_klienci(base_dir)
    generate_zamowienia(base_dir)
    generate_archiwum(base_dir)

    print("\nWygenerowano strukturę:")
    for path in sorted(base_dir.rglob("*")):
        depth = len(path.relative_to(base_dir).parts)
        prefix = "  " * depth
        print(f"{prefix}{'[dir] ' if path.is_dir() else '[file] '}{path.name}")


if __name__ == "__main__":
    main()
