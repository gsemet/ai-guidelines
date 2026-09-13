"""Tests for safe, project-owned guideline target resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_guidelines.models import GuidelineDeclaration, GuidelinesLockEntry
from ai_guidelines.paths import (
    TargetPathError,
    operation_lock_path,
    pin_target_path,
    resolve_guideline_target,
    resolve_target_path,
)


def test_default_target_prefers_agents_layout_and_falls_back_to_github(
    tmp_path: Path,
) -> None:
    """The layout fallback uses only an existing complete agents target."""
    agents_project = tmp_path / "agents"
    (agents_project / ".agents" / "guidelines").mkdir(parents=True)
    github_project = tmp_path / "github"
    github_project.mkdir()

    assert resolve_guideline_target(agents_project) == agents_project / ".agents/guidelines"
    assert resolve_guideline_target(github_project) == github_project / ".github/guidelines"


def test_pin_target_path_prefers_manifest_default_and_declaration_override(
    tmp_path: Path,
) -> None:
    """Explicit declaration targets beat the manifest-wide default."""
    declaration = GuidelineDeclaration(source="./source/")
    pinned = pin_target_path(declaration, tmp_path, ".custom/guidelines")
    assert pinned.target_path == ".custom/guidelines"

    explicit = GuidelineDeclaration(source="./source/", target_path=".project/guidelines")
    assert pin_target_path(explicit, tmp_path, ".custom/guidelines").target_path == (
        ".project/guidelines"
    )


def test_target_paths_reject_posix_windows_and_symlink_escapes(tmp_path: Path) -> None:
    """Every target spelling remains relative and contained on all hosts."""
    for unsafe in ("../outside", r"..\outside", "/tmp/outside", r"C:\outside"):
        with pytest.raises(TargetPathError):
            resolve_target_path(tmp_path, unsafe)

    outside = tmp_path.parent / "outside-target"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(TargetPathError, match="escape|project"):
        resolve_target_path(tmp_path, "link/guidelines")


def test_operation_lock_path_uses_a_project_root_dotfile(tmp_path: Path) -> None:
    """The neutral operation lock has one stable, project-owned pathname."""
    assert operation_lock_path(tmp_path) == tmp_path / ".guidelines-operation.lock"


def test_target_selection_can_retain_a_matching_lock_target(tmp_path: Path) -> None:
    """A pinned lock target remains stable when the project layout changes."""
    project = tmp_path / "project"
    project.mkdir()
    declaration = GuidelineDeclaration(source="./source/")
    entry = GuidelinesLockEntry(
        expression="./source/",
        name="source",
        source=str(project / "source"),
        source_type="local",
        resolved_ref="working-tree",
        target_path=".pinned/guidelines",
    )

    from ai_guidelines.paths import select_guideline_target

    selected = select_guideline_target(
        project,
        declaration=declaration,
        lock_entry=entry,
        default_target_path=".custom/guidelines",
    )
    assert selected == project / ".pinned/guidelines"
