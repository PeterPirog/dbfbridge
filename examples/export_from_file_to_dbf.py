"""Reconstruct DBF/FPT files while preserving the exported directory tree.

Example:
    python examples/export_from_file_to_dbf.py --source "K:\\dbf_output" \
        --output "K:\\dbf_output_reconstructed" --overwrite --progress \
        --memo inline --formats jsonl
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dbf_bridge.import_cli import main


if __name__ == "__main__":
    sys.exit(main())
