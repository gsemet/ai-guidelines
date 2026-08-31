"""Tests for safe destination planning and hash-owned reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_guidelines import atomic
from ai_guidelines.discovery import DiscoveredGuideline, discover_guidelines
from ai_guidelines.fetch import acquire_source
from ai_guidelines.locations import parse_location
from ai_guidelines.models import GuidelineFileRecord, GuidelinesLockEntry
from ai_guidelines.reconcile import (
    DestinationCollisionError,
    ReconciliationError,
    UnsafeSourcePathError,
    build_destination_map,
    reconcile_source,
)


def _write(path: Path, content: str) -> None:
    """Write one UTF-8 source or target file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def test_destination_map_flattens_and_prefers_plural_suffix(tmp_path: Path) -> None:
    """Folder sources flatten basenames and choose plural normalization."""
    source_root = tmp_path / "source"
    _write(source_root / "nested" / "team.guideline.md", "singular\n")
    _write(source_root / "team.guidelines.md", "plural\n")
    with acquire_source(parse_location(str(source_root))) as acquired:
        result = build_destination_map(
            tmp_path / "project",
            acquired,
            discover_guidelines(acquired).files,
            target_path=".agents/guidelines",
        )

    assert [item.target_path for item in result.files] == [".agents/guidelines/team.guidelines.md"]
    assert any("collision" in warning.lower() for warning in result.warnings)


def test_destination_map_rejects_explicit_multi_file_and_cross_source_collisions(
    tmp_path: Path,
) -> None:
    """No preflight map may silently collapse independent source files."""
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write(first_root / "same.guidelines.md", "first\n")
    _write(second_root / "same.guidelines.md", "second\n")
    with (
        acquire_source(parse_location(str(first_root))) as first,
        acquire_source(parse_location(str(second_root))) as second,
    ):
        first_files = discover_guidelines(first).files
        second_files = discover_guidelines(second).files
        with pytest.raises(DestinationCollisionError, match="collision"):
            build_destination_map(
                tmp_path / "project",
                [(first, first_files), (second, second_files)],
            )

    source_root = tmp_path / "many"
    _write(source_root / "one.guidelines.md", "one\n")
    _write(source_root / "two.guidelines.md", "two\n")
    with (
        acquire_source(parse_location(str(source_root))) as acquired,
        pytest.raises(ReconciliationError, match="multiple|explicit"),
    ):
        build_destination_map(
            tmp_path / "project",
            acquired,
            discover_guidelines(acquired).files,
            target_path=".agents/guidelines/custom.md",
        )


def test_destination_map_rejects_source_and_target_symlink_escapes(tmp_path: Path) -> None:
    """Source and project boundaries are checked before any file write."""
    source_root = tmp_path / "source"
    outside = tmp_path / "outside"
    _write(outside / "secret.guidelines.md", "secret\n")
    source_root.mkdir()
    escaped = source_root / "escaped.guidelines.md"
    escaped.symlink_to(outside / "secret.guidelines.md")
    with acquire_source(parse_location(str(source_root))) as acquired:
        candidate = DiscoveredGuideline(
            path=escaped,
            source_path="escaped.guidelines.md",
            suffix_stripped_name="escaped",
        )
        with pytest.raises(UnsafeSourcePathError, match="escape|source root"):
            build_destination_map(tmp_path / "project", acquired, [candidate])
    escaped.unlink()

    target_outside = tmp_path / "target-outside"
    target_outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "targets").symlink_to(target_outside, target_is_directory=True)
    _write(source_root / "safe.guidelines.md", "safe\n")
    with (
        acquire_source(parse_location(str(source_root))) as acquired,
        pytest.raises(ReconciliationError, match="escape|project"),
    ):
        build_destination_map(
            project,
            acquired,
            discover_guidelines(acquired).files,
            target_path="targets/guidelines",
        )


def test_reconcile_warns_and_overwrites_altered_managed_file(tmp_path: Path) -> None:
    """Normal sync restores source content while exposing both hashes."""
    project = tmp_path / "project"
    source_root = tmp_path / "source"
    _write(source_root / "nested" / "team.guidelines.md", "new content\n")
    target = project / ".agents/guidelines/team.guidelines.md"
    _write(target, "local edit\n")
    old_hash = hashlib.sha256(b"old content\n").hexdigest()
    current_hash = hashlib.sha256(b"local edit\n").hexdigest()
    entry = GuidelinesLockEntry(
        expression=str(source_root),
        name="source",
        source=str(source_root),
        source_type="local",
        resolved_ref="working-tree",
        target_path=".agents/guidelines",
        files=[
            GuidelineFileRecord(
                source_path="nested/team.guidelines.md",
                target_path=".agents/guidelines/team.guidelines.md",
                sha256=old_hash,
            )
        ],
    )
    with acquire_source(parse_location(str(source_root))) as acquired:
        result = reconcile_source(project, acquired, discover_guidelines(acquired).files, entry)

    assert target.read_text(encoding="utf-8") == "new content\n"
    warning = next(item for item in result.warnings if "edited" in item.lower())
    assert "nested/team.guidelines.md" in warning
    assert ".agents/guidelines/team.guidelines.md" in warning
    assert old_hash in warning
    assert current_hash in warning
    assert result.updated == [".agents/guidelines/team.guidelines.md"]


def test_reconcile_preserves_unowned_legacy_singular_target(tmp_path: Path) -> None:
    """Suffix normalization never deletes a file absent from managed records."""
    project = tmp_path / "project"
    source_root = tmp_path / "source"
    _write(source_root / "team.guideline.md", "current\n")
    legacy = project / ".agents/guidelines/team.guideline.md"
    _write(legacy, "unowned local file\n")

    with acquire_source(parse_location(str(source_root))) as acquired:
        result = reconcile_source(
            project,
            acquired,
            discover_guidelines(acquired).files,
            target_path=".agents/guidelines",
        )

    assert legacy.exists()
    assert ".agents/guidelines/team.guideline.md" not in result.removed


def test_reconcile_preserves_edited_legacy_singular_target(tmp_path: Path) -> None:
    """Suffix cleanup retains a managed legacy file after a local edit."""
    project = tmp_path / "project"
    source_root = tmp_path / "source"
    _write(source_root / "team.guideline.md", "current\n")
    legacy = project / ".agents/guidelines/team.guideline.md"
    _write(legacy, "local edit\n")
    entry = GuidelinesLockEntry(
        expression=str(source_root),
        name="source",
        source=str(source_root),
        source_type="local",
        resolved_ref="working-tree",
        target_path=".agents/guidelines",
        files=[
            GuidelineFileRecord(
                source_path="team.guideline.md",
                target_path=".agents/guidelines/team.guideline.md",
                sha256=hashlib.sha256(b"original\n").hexdigest(),
            )
        ],
    )

    with acquire_source(parse_location(str(source_root))) as acquired:
        result = reconcile_source(
            project,
            acquired,
            discover_guidelines(acquired).files,
            entry,
            target_path=".agents/guidelines",
        )

    assert legacy.read_text(encoding="utf-8") == "local edit\n"
    assert result.preserved == [".agents/guidelines/team.guideline.md"]


def test_reconcile_removes_only_unchanged_stale_files(tmp_path: Path) -> None:
    """Stale managed files are deleted only while their ownership hash matches."""
    project = tmp_path / "project"
    source_root = tmp_path / "source"
    _write(source_root / "current.guidelines.md", "current\n")
    unchanged = project / ".agents/guidelines/unchanged.guidelines.md"
    edited = project / ".agents/guidelines/edited.guidelines.md"
    _write(unchanged, "unchanged\n")
    _write(edited, "local edit\n")
    entry = GuidelinesLockEntry(
        expression=str(source_root),
        name="source",
        source=str(source_root),
        source_type="local",
        resolved_ref="working-tree",
        target_path=".agents/guidelines",
        files=[
            GuidelineFileRecord(
                source_path="unchanged.guidelines.md",
                target_path=".agents/guidelines/unchanged.guidelines.md",
                sha256=hashlib.sha256(b"unchanged\n").hexdigest(),
            ),
            GuidelineFileRecord(
                source_path="edited.guidelines.md",
                target_path=".agents/guidelines/edited.guidelines.md",
                sha256=hashlib.sha256(b"old\n").hexdigest(),
            ),
        ],
    )
    with acquire_source(parse_location(str(source_root))) as acquired:
        result = reconcile_source(project, acquired, discover_guidelines(acquired).files, entry)

    assert not unchanged.exists()
    assert edited.read_text(encoding="utf-8") == "local edit\n"
    assert result.removed == [".agents/guidelines/unchanged.guidelines.md"]
    assert result.preserved == [".agents/guidelines/edited.guidelines.md"]


def test_reconcile_file_failure_does_not_publish_metadata(tmp_path: Path, monkeypatch) -> None:
    """Per-file atomicity leaves prior successful files but skips metadata."""
    project = tmp_path / "project"
    source_root = tmp_path / "source"
    _write(source_root / "first.guidelines.md", "first\n")
    _write(source_root / "second.guidelines.md", "second\n")
    original_write = atomic.atomic_write
    metadata_calls: list[bool] = []

    def fail_second(path: Path, content: bytes) -> None:
        """Fail one write after the first file has been atomically published."""
        if path.name == "second.guidelines.md":
            raise OSError("simulated write failure")
        original_write(path, content)

    monkeypatch.setattr(atomic, "atomic_write", fail_second)
    with (
        acquire_source(parse_location(str(source_root))) as acquired,
        pytest.raises(ReconciliationError, match="completed"),
    ):
        reconcile_source(
            project,
            acquired,
            discover_guidelines(acquired).files,
            metadata_writer=lambda _result: metadata_calls.append(True),
        )

    assert (project / ".github/guidelines/first.guidelines.md").read_text(encoding="utf-8") == (
        "first\n"
    )
    assert not (project / ".github/guidelines/second.guidelines.md").exists()
    assert metadata_calls == []
