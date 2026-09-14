"""Tests for sparse-checkout selector expansion."""

from __future__ import annotations

from ai_guidelines.models import GuidelineDeclaration
from ai_guidelines.sparse import selector_sparse_patterns


def test_plural_selectors_expand_to_files_and_folders() -> None:
    """Expand plural selectors into literal sparse-checkout patterns."""
    declaration = GuidelineDeclaration(
        source="github/example/repo", paths=["guidelines/engineering/team"]
    )

    assert selector_sparse_patterns(declaration) == [
        "guidelines/engineering/team",
        "guidelines/engineering/team/*",
        "guidelines/engineering/team.guideline.md",
        "guidelines/engineering/team.guidelines.md",
    ]


def test_legacy_pattern_and_location_only_remain_distinct() -> None:
    """Keep legacy patterns separate from declarations without selectors."""
    pattern = GuidelineDeclaration(source="github/example/repo", pattern="python_*")
    location = GuidelineDeclaration(source="github/example/repo")

    assert selector_sparse_patterns(pattern) == [
        "**/python_*.guideline.md",
        "**/python_*.guidelines.md",
    ]
    assert selector_sparse_patterns(location) == []
