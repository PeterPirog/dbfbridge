"""Run a diagnostic DBF -> JSONL -> DBF round-trip.

Example:
    python examples/check_conversion_quality.py --source "K:\\dbf_source" \
        --output "K:\\dbf_quality" --overwrite --progress
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbf_bridge.quality import main


if __name__ == "__main__":
    sys.exit(main())
