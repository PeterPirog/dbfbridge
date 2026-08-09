"""
examples/export_dbf.py
======================

Przykład uruchomienia konwertera dbf_bridge na rzeczywistych danych
(Visual FoxPro 9.0 SP2, pliki DBF/FPT/CDX z polskimi znakami).

Uruchamianie z PyCharm:
    Dodaj wymagane argumenty w konfiguracji „Run/Debug", np.:
        --source "D:\\InneDane" --output "D:\\Wynik"

Uruchamianie z linii poleceń:
    python examples/export_dbf.py --source "K:\\dbf_source" --output "K:\\dbf_output" --formats jsonl
    python examples/export_dbf.py --source "K:\\dbf_source" --output "K:\\dbf_output" --formats jsonl --incremental

Wymagania:
    pip install dbfbridge

Wynik:
    W katalogu output powstają pliki CSV, JSON, JSONL (zależnie od --formats)
    z zachowaniem struktury katalogów źródłowych, pliki <nazwa>_schema.json
    z metadanymi DBF oraz migration_report.jsonl/.csv z raportem migracji
    (SHA-256, liczniki rekordów, statystyki null/memo). Plik
    conversion_checksums.json umożliwia późniejszy eksport przyrostowy.
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
    sys.exit(main())
