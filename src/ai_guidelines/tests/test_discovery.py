"""Tests for recursive guideline discovery and selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_guidelines.discovery import DiscoveryError, discover_guidelines, is_guideline_file
from ai_guidelines.fetch import acquire_source
from ai_guidelines.locations import parse_location


def _write(path: Path, content: str = "# Body\n") -> None:
    """Create a fixture file and its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_folder_discovery_filters_exact_suffixes_and_retains_namespace(tmp_path: Path) -> None:
    """Discover supported suffixes while preserving nested source paths."""
    source = tmp_path / "source"
    _write(
        source / "engineering" / "git.guideline.md",
        "---\nname: Git\ndescription: Git rules\nmetadata:\n  owner: team\n---\n# Git\n",
    )
    _write(source / "engineering" / "python.guidelines.md")
    _write(source / "README.md", "not a guideline\n")
    _write(source / "UPPER.GUIDELINE.MD", "wrong case\n")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(acquired)

    assert [item.source_path for item in result.files] == [
        "engineering/git.guideline.md",
        "engineering/python.guidelines.md",
    ]
    assert result.files[0].description == "Git rules"
    assert result.files[0].metadata["owner"] == "team"
    assert result.files[0].suffix_stripped_name == "git"
    assert not result.warnings


def test_pattern_matches_suffix_stripped_basename_case_sensitively(tmp_path: Path) -> None:
    """Match patterns against suffix-stripped basenames case-sensitively."""
    source = tmp_path / "source"
    _write(source / "git_hooks.guideline.md")
    _write(source / "git_hooks.guidelines.md")
    _write(source / "Git_hooks.guidelines.md")
    _write(source / "git_hooks.txt")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(acquired, pattern="git_*")

    assert [item.source_path for item in result.files] == [
        "git_hooks.guideline.md",
        "git_hooks.guidelines.md",
    ]


def test_pattern_matches_guideline_question_mark_suffix(tmp_path: Path) -> None:
    """A single-character suffix glob selects both supported filename forms."""
    _write(tmp_path / "team.guideline.md")
    _write(tmp_path / "team.guidelines.md")

    with acquire_source(parse_location(str(tmp_path))) as acquired:
        result = discover_guidelines(acquired, pattern="team.guideline?.md")

    assert [item.filename for item in result.files] == [
        "team.guideline.md",
        "team.guidelines.md",
    ]


def test_paths_select_literal_and_suffixless_source_paths(tmp_path: Path) -> None:
    """Select literal files using both suffixed and suffixless paths."""
    source = tmp_path / "source"
    selected_file = source / "guidelines" / "Engineering" / "Git" / "git.guideline.md"
    selected_stem = source / "guidelines" / "Engineering" / "Python" / "python.guideline.md"
    _write(selected_file)
    _write(selected_stem)
    _write(source / "guidelines" / "Engineering" / "Other" / "other.guideline.md")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(
            acquired,
            paths=[
                "guidelines/Engineering/Git/git.guideline.md",
                "guidelines/Engineering/Python/python",
            ],
        )

    assert [item.source_path for item in result.files] == [
        "git.guideline.md",
        "python.guideline.md",
    ]


def test_directory_selector_flattens_source_folder(tmp_path: Path) -> None:
    """Flatten a selected directory while retaining nested relative paths."""
    source = tmp_path / "source"
    _write(source / "guidelines" / "one.guideline.md")
    _write(source / "guidelines" / "nested" / "two.guidelines.md")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(acquired, paths=["guidelines"])

    assert [item.source_path for item in result.files] == [
        "one.guideline.md",
        "nested/two.guidelines.md",
    ]


def test_empty_selection_returns_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Report a warning when a selector matches no guideline files."""
    source = tmp_path / "source"
    _write(source / "python.guidelines.md")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(acquired, pattern="git_*")

    assert result.files == []
    assert "no guideline files matched" in result.warnings[0].lower()
    assert "no guideline files matched" in caplog.text.lower()


def test_malformed_optional_frontmatter_is_nonfatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep discovering a guideline when optional frontmatter is malformed."""
    source = tmp_path / "source"
    _write(source / "broken.guidelines.md", "---\nname: [unterminated\n---\n# Body\n")

    with acquire_source(parse_location(str(source))) as acquired:
        result = discover_guidelines(acquired)

    assert [item.source_path for item in result.files] == ["broken.guidelines.md"]
    assert result.files[0].name == "broken"
    assert result.files[0].metadata == {}
    assert result.files[0].frontmatter == {}
    assert "frontmatter" in caplog.text.lower()


def test_single_file_and_unrelated_file_are_supported(tmp_path: Path) -> None:
    """Handle a selected guideline file and ignore an unrelated file."""
    supported = tmp_path / "one.guidelines.md"
    unrelated = tmp_path / "one.txt"
    _write(supported)
    _write(unrelated)

    with acquire_source(parse_location(str(supported))) as acquired:
        supported_result = discover_guidelines(acquired)
    with acquire_source(parse_location(str(unrelated))) as acquired:
        unrelated_result = discover_guidelines(acquired)

    assert [item.source_path for item in supported_result.files] == ["one.guidelines.md"]
    assert unrelated_result.files == []
    assert unrelated_result.warnings


def test_supported_suffixes_are_case_sensitive() -> None:
    """Accept only the two documented lowercase guideline suffixes."""
    assert is_guideline_file("a.guideline.md")
    assert is_guideline_file("a.guidelines.md")
    assert not is_guideline_file("a.GUIDELINE.md")
    assert not is_guideline_file("a.guideline.MD")
    assert not is_guideline_file("a.md")


def test_discovery_rejects_symlink_outside_source(tmp_path: Path) -> None:
    """Reject discovered files whose symlinks escape the source root."""
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    source.mkdir()
    _write(outside / "secret.guidelines.md", "# Secret\n")
    (source / "external.guidelines.md").symlink_to(outside / "secret.guidelines.md")

    with (
        acquire_source(parse_location(str(source))) as acquired,
        pytest.raises(DiscoveryError, match="escape|outside|contain"),
    ):
        discover_guidelines(acquired)
