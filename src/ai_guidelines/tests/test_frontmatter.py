"""Tests for permissive guideline frontmatter reading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_guidelines.frontmatter import read_guideline_frontmatter, read_guideline_meta


def _write(path: Path, content: str) -> Path:
    """Write a UTF-8 frontmatter fixture and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_read_frontmatter_with_optional_metadata(tmp_path: Path) -> None:
    """Read name, description, and nested metadata from frontmatter."""
    path = _write(
        tmp_path / "team.guideline.md",
        "---\nname: Team\ndescription: Team rules\nmetadata:\n  owner: tools\n---\n# Team\n",
    )

    metadata = read_guideline_frontmatter(path)

    assert metadata["name"] == "Team"
    assert metadata["description"] == "Team rules"
    assert metadata["metadata"]["owner"] == "tools"


def test_read_without_frontmatter_returns_empty_mapping(tmp_path: Path) -> None:
    """Return empty metadata mappings when a file has no frontmatter."""
    path = _write(tmp_path / "plain.guideline.md", "# Plain\n")

    assert read_guideline_frontmatter(path) == {}
    assert read_guideline_meta(path) == {}


def test_malformed_optional_frontmatter_is_reported_to_caller(tmp_path: Path) -> None:
    """Expose malformed YAML to the low-level reader."""
    path = _write(tmp_path / "bad.guideline.md", "---\nname: [broken\n---\n# Body\n")

    # The discovery layer is permissive and converts this failure to a warning;
    # the low-level reader keeps parsing failures observable to its caller.
    with pytest.raises(yaml.YAMLError):
        read_guideline_frontmatter(path)
