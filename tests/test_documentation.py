from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import dbfbridge
from dbf_bridge import __version__

ROOT = Path(__file__).parents[1]


def test_documentation_versions_and_entry_points_match_package_metadata() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert metadata["version"] == __version__
    assert f"**{__version__} (alpha)**" in readme
    for command in metadata["scripts"]:
        assert f"`{command}`" in readme
    for function in (
        "export_dbf",
        "reconstruct_dbf",
        "verify_conversion",
        "check_conversion_quality",
    ):
        assert function in dbfbridge.__all__
        assert f"`{function}()`" in readme


def test_relative_markdown_links_resolve() -> None:
    missing: list[str] = []
    for document in ROOT.rglob("*.md"):
        if any(part in {".git", ".venv", "build", "dist"} for part in document.parts):
            continue
        text = document.read_text(encoding="utf-8")
        for raw_target in re.findall(r"!?(?:\[[^\]]*\])\(([^)]+)\)", text):
            target = raw_target.strip().split()[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative and not (document.parent / relative).exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")

    assert not missing, "Missing relative Markdown targets:\n" + "\n".join(missing)


def test_sample_commands_reference_existing_scripts() -> None:
    commands = (ROOT / "examples" / "sample_commands.txt").read_text(encoding="utf-8")
    scripts = re.findall(r"python\s+(examples/[\w_]+\.py)", commands)

    assert scripts
    assert all((ROOT / script).is_file() for script in scripts)
