"""Documentation-quality regression tests: internal links and anchors.

Every internal reference inside the maintained Markdown documentation must
resolve WITHOUT network access:

- relative file targets exist;
- relative ``#anchors`` resolve against GitHub-style heading slugs;
- same-repository GitHub ``blob/main/<path>`` targets exist locally;
- same-repository ``blob/main/<path>#anchor`` anchors resolve locally.

Dynamic external links (Actions run pages, issues, third-party sites, PyPI)
are skipped. No network is used.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]

MAINTAINED_DOCS = (
    "README.md",
    "AGENTS.md",
    "PUBLISHING.md",
    "examples/README.md",
    "benchmarks/README.md",
    "docs/README.md",
    "docs/pypi-usage.md",
    "docs/python-api-examples.md",
    "docs/tool-server-integration.md",
    "docs/api-1.0.md",
    "docs/compatibility-vfp.md",
    "docs/migration-1.0.md",
    "docs/architecture-closure.md",
)

GITHUB_BLOB_RE = re.compile(
    r"https://github\.com/PeterPirog/dbfbridge/blob/main/([^\s)#]+?)(?:#([^\s)]+))?\)"
)


def _markdown_files() -> list[Path]:
    files = [ROOT / relative for relative in MAINTAINED_DOCS]
    files.extend((ROOT / "docs" / "architecture").glob("*.md"))
    return [path for path in files if path.is_file()]


def _heading_slug(heading: str) -> str:
    """GitHub-style heading normalization (sufficient for this repository):
    lowercase, strip formatting/backticks, remove punctuation except word
    characters and hyphens, spaces -> hyphens. Consecutive hyphens (from
    em-dashes etc.) are preserved, exactly like GitHub."""
    text = heading.strip().lstrip("#").strip().lower()
    text = re.sub(r"`+", "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.ASCII)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def _document_slugs(document: Path) -> set[str]:
    return {
        _heading_slug(line)
        for line in document.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    }


def test_internal_relative_links_resolve() -> None:
    problems: list[str] = []
    for document in _markdown_files():
        base = document.parent
        text = document.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in re.finditer(r"\]\(([^)\s]+)\)", line):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                resolved = (base / target).resolve()
                if not resolved.is_file():
                    problems.append(f"{document.name}:{number}: missing {target!r}")
    assert problems == [], "broken internal links:\n" + "\n".join(problems)


def test_internal_anchors_resolve() -> None:
    problems: list[str] = []
    for document in _markdown_files():
        own_slugs = _document_slugs(document)
        text = document.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in re.finditer(r"\]\(#([^\s)]+)\)", line):
                anchor = match.group(1)
                if anchor not in own_slugs:
                    problems.append(
                        f"{document.name}:{number}: unresolved anchor #{anchor!r}"
                    )
    assert problems == [], "unresolved internal anchors:\n" + "\n".join(problems)


def test_relative_md_links_with_anchors_resolve() -> None:
    problems: list[str] = []
    for document in _markdown_files():
        base = document.parent
        text = document.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in re.finditer(r"\]\(([^)#\s]+\.md)#([^\s)]+)\)", line):
                relative, anchor = match.group(1), match.group(2)
                target = base / relative
                if not target.is_file():
                    continue  # missing files reported by the link test
                if anchor not in _document_slugs(target):
                    problems.append(
                        f"{document.name}:{number}: unresolved anchor #{anchor!r} in {relative!r}"
                    )
    assert problems == [], "unresolved cross-file anchors:\n" + "\n".join(problems)


def test_same_repository_github_links_resolve_locally() -> None:
    problems: list[str] = []
    for document in _markdown_files():
        text = document.read_text(encoding="utf-8")
        for match in GITHUB_BLOB_RE.finditer(text):
            relative = match.group(1)
            target = ROOT / relative
            if not target.exists():
                problems.append(
                    f"{document.name}: same-repo link target missing: {relative!r}"
                )
    assert problems == [], "broken same-repository GitHub links:\n" + "\n".join(problems)


GITHUB_BLOB_RE = re.compile(
    r"https://github\.com/PeterPirog/dbfbridge/blob/main/([^\s)#]+)"
)


def test_same_repository_github_anchors_resolve() -> None:
    problems: list[str] = []
    for document in _markdown_files():
        text = document.read_text(encoding="utf-8")
        for match in re.finditer(
            r"https://github\.com/PeterPirog/dbfbridge/blob/main/([^\s)#]+\.md)#([^\s)]+)\)",
            text,
        ):
            relative, anchor = match.group(1), match.group(2)
            target = ROOT / relative
            if target.is_file() and anchor not in _document_slugs(target):
                problems.append(
                    f"{document.name}: unresolved anchor #{anchor!r} in {relative!r}"
                )
    assert problems == [], "unresolved same-repo GitHub anchors:\n" + "\n".join(problems)


def test_heading_slug_normalization_is_deterministic() -> None:
    assert _heading_slug("## Choose the install profile") == "choose-the-install-profile"
    assert _heading_slug("### `iter_records()` — streaming") == "iter_records-streaming"
    assert _heading_slug("## Path security (host responsibility)") == (
        "path-security-host-responsibility"
    )


def test_maintained_documentation_is_english() -> None:
    """Narrow regression for the previously Polish user documentation: the
    maintained files must not carry the old Polish section headings. Legitimate
    Polish DBF sample text and codec names stay allowed; CHANGELOG history is
    also allowed to name historical documents."""
    forbidden_headings = (
        "Przykłady",
        "Użycie po instalacji",
        "Uruchomienie w PowerShell",
        "Dane testowe",
        "Użycie jako biblioteka",
        "Przykłady repozytorium",
    )
    for document in _markdown_files():
        text = document.read_text(encoding="utf-8")
        for marker in forbidden_headings:
            assert marker not in text, f"{document.name} contains Polish text: {marker!r}"
