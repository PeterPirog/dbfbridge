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
from dbf_bridge.verifier import build_parser as build_verifier_parser

ROOT = Path(__file__).parents[1]


def test_documentation_versions_and_entry_points_match_package_metadata() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert metadata["version"] == __version__
    # The README status blockquote must carry the current package version
    # (release-stage neutral: the maturity marker may change with the
    # release stage — see test_release_readiness.py).
    status_lines = [
        line.strip()
        for line in readme.splitlines()
        if line.strip().startswith(">") and "Status" in line
    ]
    assert any(metadata["version"] in line for line in status_lines), readme.splitlines()[:14]
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


def test_verifier_requires_portable_source_and_output_paths() -> None:
    parser = build_verifier_parser()
    actions = {option: action for action in parser._actions for option in action.option_strings}

    assert actions["--source"].required
    assert actions["--output"].required
