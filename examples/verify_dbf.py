"""
examples/verify_dbf.py
======================

Przykład uruchomienia weryfikatora dbf_bridge na wynikach konwersji
danych DBF. Sprawdza, czy eksport DBF -> CSV/JSON/JSONL
zakończył się poprawnie (liczba rekordów, SHA-256, schema, składnia).

Uruchamianie:
    python examples/verify_dbf.py --source "K:\\dbf_source" --output "K:\\dbf_output"
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
    sys.exit(main())
