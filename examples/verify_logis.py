"""
examples/verify_logis.py
========================

Przykład uruchomienia weryfikatora dbf_bridge na wynikach konwersji
danych systemu Logis. Sprawdza, czy eksport DBF -> CSV/JSON/JSONL
zakończył się poprawnie (liczba rekordów, SHA-256, schema, składnia).

Domyślne ścieżki:
    source = K:\\Logis       — katalog z oryginalnymi plikami DBF
    output = K:\\Logis_out   — katalog z wynikami konwersji

Uruchamianie:
    python examples/verify_logis.py
    python examples/verify_logis.py --source "K:\\Logis" --output "K:\\Logis_out"
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbf_bridge.verifier import main


if __name__ == "__main__":
    default_args = [
        "--source", r"K:\Logis",
        "--output", r"K:\Logis_out",
        "--formats", "csv,json,jsonl",
    ]
    cli_args = sys.argv[1:] if len(sys.argv) > 1 else default_args
    sys.exit(main(cli_args))