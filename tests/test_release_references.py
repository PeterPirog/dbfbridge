"""Release-reference regressions: CHANGELOG release links and truthfulness
of current maintained documentation about tags/publication."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]

CHANGELOG = ROOT / "CHANGELOG.md"
MAINTAINED_DOCS = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs" / "pypi-usage.md",
    ROOT / "docs" / "architecture-closure.md",
    ROOT / "docs" / "tool-server-integration.md",
    ROOT / "docs" / "python-api-examples.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "api-1.0.md",
)


def test_changelog_does_not_reference_nonexistent_v010() -> None:
    """The v0.1.0 tag does not exist in Git; the CHANGELOG must not link to it."""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert "v0.1.0" not in changelog
    assert "releases/tag/v0.1.0" not in changelog


def test_changelog_contains_valid_historical_v020_release_url() -> None:
    """v0.2.0 is an EXISTING annotated tag with an EXISTING GitHub Release —
    the changelog link must point to the real release page."""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert (
        "[0.2.0]: https://github.com/PeterPirog/dbfbridge/releases/tag/v0.2.0"
        in changelog
    )


def test_current_state_docs_distinguish_release_from_publication() -> None:
    """Maintained current-state docs must distinguish the EXISTING GitHub
    Release v0.2.0 from the NOT-COMPLETED PyPI publication."""
    for path in MAINTAINED_DOCS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        # the false/ambiguous absolute claims must not return
        assert "no tag exists" not in lowered, path.name
        assert "nothing is published" not in lowered, path.name
        assert "never been released" not in lowered, path.name


def test_release_history_distinguishes_github_release_from_pypi() -> None:
    """The architecture-closure release-history section must state the real
    facts: the v0.2.0 GitHub Release/tag exists, the PyPI publish step failed,
    v0.3.0 was never released, v1.0.0 is not yet released."""
    closure = (ROOT / "docs" / "architecture-closure.md").read_text(encoding="utf-8")
    assert "v0.2.0" in closure and "2d0ad653edbdb9341c96c19f323f893678819550" in closure
    assert "33487949133" in closure
    assert "invalid-publisher" in closure
    assert "never released" in closure
    assert "not yet released" in closure
    # The failed publish step is distinguished from the successful build
    # (case-insensitive on purpose: the wording must not depend on casing).
    lowered = closure.lower()
    assert "build success" in lowered
    assert "publish failed" in lowered


def test_changelog_footer_links_only_real_refs() -> None:
    """The changelog footer must keep the real v0.2.0 release link and must
    never link to a tag that does not exist in Git (v0.1.0 / v0.3.0 /
    v1.0.0)."""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    assert (
        "[Unreleased]: https://github.com/PeterPirog/dbfbridge/compare/v0.2.0...HEAD"
        in changelog
    )
    assert (
        "[0.2.0]: https://github.com/PeterPirog/dbfbridge/releases/tag/v0.2.0"
        in changelog
    )
    for nonexistent in ("v0.1.0", "v0.3.0", "v1.0.0"):
        assert f"releases/tag/{nonexistent}" not in changelog
        assert f"compare/{nonexistent}" not in changelog


def test_maintained_docs_do_not_link_nonexistent_release_tags() -> None:
    """No maintained document may point to a release page for a tag that was
    never created (v0.1.0 / v0.3.0 / v1.0.0). Historical documents are out of
    scope; this covers the current maintained set only."""
    problems: list[str] = []
    for path in MAINTAINED_DOCS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for nonexistent in ("v0.1.0", "v0.3.0", "v1.0.0"):
            marker = f"releases/tag/{nonexistent}"
            if marker in text:
                problems.append(f"{path.name}: references {marker}")
            marker = f"compare/{nonexistent}"
            if marker in text:
                problems.append(f"{path.name}: references {marker}")
    assert problems == []


def test_no_doc_claims_the_deferred_releases_as_existing() -> None:
    """The final 1.0.0 release/tag is intentionally deferred and v0.3.0 was
    never created: no maintained document may claim the opposite. Narrow,
    semantic assertions — prose is not frozen."""
    for path in MAINTAINED_DOCS:
        if not path.is_file():
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        assert "v0.3.0 is released" not in lowered, path.name
        assert "v0.3.0 was published" not in lowered, path.name
        assert "v1.0.0 is released" not in lowered, path.name
        assert "v1.0.0 is published" not in lowered, path.name
        assert "1.0 is currently available on pypi" not in lowered, path.name
