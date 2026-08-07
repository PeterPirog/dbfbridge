"""
examples/export_logis.py
========================

Przykład uruchomienia konwertera dbf_bridge na rzeczywistych danych
systemu Logis (Visual FoxPro 9.0 SP2, pliki DBF/FPT/CDX z polskimi znakami).

Domyślne ścieżki (do edycji w razie potrzeby):
    source = K:\\Logis       — katalog z plikami DBF systemu Logis
    output = K:\\Logis_out   — katalog wyjściowy (CSV/JSON/JSONL)

Uruchamianie z PyCharm:
    Otwórz ten plik w PyCharm i kliknij „Run" — skrypt użyje domyślnych
    ścieżek. Aby zmienić parametry, edytuj konfigurację „Run/Debug"
    i dodaj argumenty w polu „Parameters", np.:
        --source "D:\\InneDane" --output "D:\\Wynik"

Uruchamianie z linii poleceń:
    python examples/export_logis.py
    python examples/export_logis.py --source "K:\\Logis" --output "K:\\Logis_out" --formats jsonl

Wymagania:
    pip install dbfbridge

Wynik:
    W katalogu output powstają pliki CSV, JSON, JSONL (zależnie od --formats)
    z zachowaniem struktury katalogów źródłowych, pliki .schema.jsonl
    z metadanymi DBF oraz migration_report.jsonl/.csv z raportem migracji
    (SHA-256, liczniki rekordów, statystyki null/memo).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Dodaj katalog src/ do sys.path, aby działało bez `pip install`
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbf_bridge.cli import main


if __name__ == "__main__":
    # Domyślne argumenty — używane gdy skrypt uruchomiony bez parametrów
    # (np. kliknięcie „Run" w PyCharm). Z linii poleceń można nadpisać.
    default_args = [
        "--source", r"K:\Logis",
        "--output", r"K:\Logis_out",
        "--formats", "csv,json,jsonl",
        "--overwrite",
    ]

    # Jeśli podano argumenty w linii poleceń, użyj ich; w przeciwnym razie
    # użyj domyślnych ścieżek Logis.
    cli_args = sys.argv[1:] if len(sys.argv) > 1 else default_args
    sys.exit(main(cli_args))