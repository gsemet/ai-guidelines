"""Tests for reproducible manifest synchronization."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.manifest import save_manifest
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
    GuidelinesManifest,
)
from ai_guidelines.sync import FrozenSyncError, SyncError, sync_manifest


def _write(path: Path, content: str) -> None:
    """Write one UTF-8 file and create its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def _manifest(
    project: Path, *declarations: GuidelineDeclaration, default: str | None = None
) -> None:
    """Persist a test manifest."""
    save_manifest(
        project / "guidelines.yml",
        GuidelinesManifest(default_guidelines_path=default, guidelines=list(declarations)),
    )


class FakeFetcher:
    """Return a deterministic remote materialization without network access."""

    def __init__(self, source_root: Path, commit: str = "a" * 40) -> None:
        self.source_root = source_root
        self.commit = commit
        self.requested_refs: list[str | None] = []

    @contextmanager
    def acquire_many(self, locations, *, sparse_patterns=None) -> Iterator[list[AcquiredSource]]:
        """Yield one acquired source per requested location."""
        del sparse_patterns
        self.requested_refs.extend(location.requested_ref for location in locations)
        yield [
            AcquiredSource(
                location=location,
                root=self.source_root,
                path=self.source_root,
                resolved_ref=location.requested_ref,
                commit=self.commit,
                reference_kind="branch",
            )
            for location in locations
        ]


def test_sync_creates_neutral_lock_and_managed_file(tmp_path: Path) -> None:
    """An ordinary local sync publishes files and neutral lock metadata."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "team\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))

    result = sync_manifest(project)

    target = project / ".github/guidelines/team.guidelines.md"
    assert target.read_text(encoding="utf-8") == "team\n"
    assert result.lock_written
    assert result.lockfile.manager == "ai-guidelines"
    assert load_lockfile(project / "guidelines.lock.json").guidelines[0].is_complete()


def test_sync_target_precedence_is_declaration_lock_default_agents_then_github(
    tmp_path: Path,
) -> None:
    """Each target precedence level is selected without escaping the project."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "team\n")
    (project / ".agents/guidelines").mkdir(parents=True)

    declaration = GuidelineDeclaration(source="./source/", target_path=".declared/guidelines")
    _manifest(project, declaration, default=".default/guidelines")
    sync_manifest(project)
    assert (project / ".declared/guidelines/team.guidelines.md").exists()

    declaration.target_path = None
    _manifest(project, declaration, default=".default/guidelines")
    sync_manifest(project)
    assert (project / ".declared/guidelines/team.guidelines.md").exists()

    (project / "guidelines.lock.json").unlink()
    _manifest(project, GuidelineDeclaration(source="./source/"), default=".default/guidelines")
    sync_manifest(project)
    assert (project / ".default/guidelines/team.guidelines.md").exists()

    (project / "guidelines.lock.json").unlink()
    _manifest(project, GuidelineDeclaration(source="./source/"))
    sync_manifest(project)
    assert (project / ".agents/guidelines/team.guidelines.md").exists()

    (project / "guidelines.lock.json").unlink()
    (project / ".agents/guidelines/team.guidelines.md").unlink()
    (project / ".agents/guidelines").rmdir()
    sync_manifest(project)
    assert (project / ".github/guidelines/team.guidelines.md").exists()


def test_sync_detects_collisions_before_project_writes(tmp_path: Path) -> None:
    """A cross-source collision prevents both managed files and lock output."""
    project = tmp_path / "project"
    project.mkdir()
    first = project / "first"
    second = project / "second"
    _write(first / "same.guidelines.md", "first\n")
    _write(second / "same.guidelines.md", "second\n")
    _manifest(
        project, GuidelineDeclaration(source="./first/"), GuidelineDeclaration(source="./second/")
    )

    with pytest.raises(SyncError, match="collision"):
        sync_manifest(project)

    assert not (project / ".github/guidelines/same.guidelines.md").exists()
    assert not (project / "guidelines.lock.json").exists()


def test_dry_run_has_the_same_plan_without_project_writes(tmp_path: Path) -> None:
    """Dry-run exposes prospective actions while leaving project state untouched."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "team\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))

    result = sync_manifest(project, dry_run=True)

    assert result.dry_run
    assert result.plan.entries[0].actions["added"] == [".github/guidelines/team.guidelines.md"]
    assert result.total_files == 1
    assert result.changed_files == 1
    assert result.actions["added"] == [".github/guidelines/team.guidelines.md"]
    assert not (project / "guidelines.lock.json").exists()
    assert not (project / ".github/guidelines").exists()


def test_sync_adoption_records_hash_for_the_next_run(tmp_path: Path) -> None:
    """A real adoption persists ownership so the next sync is quiet."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "source\n")
    declaration = GuidelineDeclaration(source="./source/")
    _manifest(project, declaration)
    first = sync_manifest(project)
    first_record = first.lockfile.guidelines[0].files[0]
    incomplete_record = first_record.model_copy(update={"sha256": None})
    incomplete_entry = first.lockfile.guidelines[0].model_copy(
        update={"files": [incomplete_record]}
    )
    save_lockfile(
        project / "guidelines.lock.json",
        first.lockfile.model_copy(update={"guidelines": [incomplete_entry]}),
    )
    target = project / ".github" / "guidelines" / "team.guidelines.md"
    target.write_text("locally edited\n", encoding="utf-8")

    result = sync_manifest(project)

    assert result.reconciliations[0].adopted == [".github/guidelines/team.guidelines.md"]
    assert not any("adopt" in warning.lower() for warning in result.warnings)
    next_result = sync_manifest(project)
    assert next_result.reconciliations[0].adopted == []
    assert not any("adopt" in warning.lower() for warning in next_result.warnings)


def test_dry_run_reports_adoption_without_warning(tmp_path: Path) -> None:
    """Dry-run adoption is reported as an action without emitting a warning."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "source\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))
    target = project / ".github" / "guidelines" / "team.guidelines.md"
    target.parent.mkdir(parents=True)
    target.write_text("pre-existing\n", encoding="utf-8")

    result = sync_manifest(project, dry_run=True)

    assert result.reconciliations[0].adopted == [".github/guidelines/team.guidelines.md"]
    assert not any("adopt" in warning.lower() for warning in result.warnings)
    assert target.read_text(encoding="utf-8") == "pre-existing\n"


def test_frozen_missing_state_fails_before_acquisition_or_writes(tmp_path: Path) -> None:
    """Frozen mode rejects incomplete replay state without invoking a fetcher."""
    project = tmp_path / "project"
    project.mkdir()
    _manifest(
        project,
        GuidelineDeclaration(source="https://example.com/team/repo", ref="a" * 40),
    )

    class FailingFetcher:
        """Fail the test if frozen mode attempts acquisition."""

        def acquire_many(self, *_args, **_kwargs):
            """Make forbidden source acquisition observable."""
            raise AssertionError("frozen mode acquired a source")

    with pytest.raises(FrozenSyncError, match="lock"):
        sync_manifest(project, frozen=True, fetcher=FailingFetcher())

    assert not (project / "guidelines.lock.json").exists()
    assert not (project / ".github/guidelines").exists()


def test_frozen_rejects_lock_entry_without_target_provenance(tmp_path: Path) -> None:
    """Frozen mode requires the recorded target before inspecting sources."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "team\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))
    sync_manifest(project)

    lock = load_lockfile(project / "guidelines.lock.json")
    incomplete = lock.guidelines[0].model_copy(update={"target_path": None})
    save_lockfile(
        project / "guidelines.lock.json",
        lock.model_copy(update={"guidelines": [incomplete]}),
    )

    with pytest.raises(FrozenSyncError, match="target|incomplete"):
        sync_manifest(project, frozen=True)


def test_frozen_rejects_changed_source_content(tmp_path: Path) -> None:
    """Frozen mode rejects source content that differs from locked hashes."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    source_file = source / "team.guidelines.md"
    _write(source_file, "original\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))
    sync_manifest(project)
    _write(source_file, "changed\n")

    with pytest.raises(FrozenSyncError, match="hash|source|drift"):
        sync_manifest(project, frozen=True)


def test_locked_replay_uses_recorded_commit_and_lock_publication_is_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete lock pins the fetch request and is saved after target files."""
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write(source / "team.guidelines.md", "team\n")
    _manifest(
        project,
        GuidelineDeclaration(source="https://example.com/team/repo", ref="a" * 40),
    )
    fetcher = FakeFetcher(source)
    events: list[str] = []

    original_save = save_lockfile

    def record_save(path, lockfile) -> None:
        """Observe managed files before lock publication."""
        events.append("lock")
        assert (project / ".github/guidelines/team.guidelines.md").exists()
        original_save(path, lockfile)

    monkeypatch.setattr("ai_guidelines.sync.save_lockfile", record_save)
    first = sync_manifest(project, fetcher=fetcher)
    events.clear()
    sync_manifest(project, fetcher=fetcher)

    assert fetcher.requested_refs == ["a" * 40, "a" * 40]
    assert first.lockfile.guidelines[0].commit == "a" * 40
    assert events == ["lock"]


def test_moving_branch_refreshes_lock_provenance(tmp_path: Path) -> None:
    """A moving branch is reacquired and records its newly resolved commit."""
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write(source / "team.guidelines.md", "team\n")
    _manifest(
        project,
        GuidelineDeclaration(source="https://example.com/team/repo", ref="main"),
    )
    fetcher = FakeFetcher(source, commit="a" * 40)

    first = sync_manifest(project, fetcher=fetcher)
    fetcher.commit = "b" * 40
    second = sync_manifest(project, fetcher=fetcher)

    assert fetcher.requested_refs == ["main", "main"]
    assert first.lockfile.guidelines[0].commit == "a" * 40
    assert second.lockfile.guidelines[0].commit == "b" * 40


def test_lock_publication_failure_preserves_previous_lock(tmp_path: Path, monkeypatch) -> None:
    """Atomic lock failure retains prior bytes while files may already change."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "old\n")
    _manifest(project, GuidelineDeclaration(source="./source/"))
    sync_manifest(project)
    old_lock = (project / "guidelines.lock.json").read_bytes()
    _write(source / "team.guidelines.md", "new\n")

    def fail_save(*_args, **_kwargs) -> None:
        """Inject publication failure after reconciliation."""
        raise OSError("simulated lock failure")

    monkeypatch.setattr("ai_guidelines.sync.save_lockfile", fail_save)
    with pytest.raises(SyncError, match="lock|publish"):
        sync_manifest(project)

    assert (project / "guidelines.lock.json").read_bytes() == old_lock
    assert (project / ".github/guidelines/team.guidelines.md").read_text(encoding="utf-8") == (
        "new\n"
    )


def test_removed_declaration_does_not_delete_its_files(tmp_path: Path) -> None:
    """Declaration removal is non-destructive; only source staleness cleans files."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guidelines.md", "team\n")
    declaration = GuidelineDeclaration(source="./source/")
    _manifest(project, declaration)
    sync_manifest(project)
    target = project / ".github/guidelines/team.guidelines.md"

    _manifest(project)
    sync_manifest(project)

    assert target.exists()


def test_sync_cleans_up_legacy_singular_target_after_normalization(tmp_path: Path) -> None:
    """Suffix normalization removes an owned old singular target."""
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source"
    _write(source / "team.guideline.md", "current\n")
    target = project / ".agents/guidelines"
    target.mkdir(parents=True)
    legacy = target / "team.guideline.md"
    legacy.write_text("legacy content\n", encoding="utf-8")
    declaration = GuidelineDeclaration(source="./source/", target_path=".agents/guidelines")
    _manifest(project, declaration)
    save_lockfile(
        project / "guidelines.lock.json",
        GuidelinesLock(
            guidelines=[
                GuidelinesLockEntry(
                    expression=declaration.source,
                    name="source",
                    source=source.as_posix(),
                    source_type="local",
                    resolved_ref="working-tree",
                    target_path=".agents/guidelines",
                    files=[
                        GuidelineFileRecord(
                            source_path="team.guideline.md",
                            target_path=".agents/guidelines/team.guideline.md",
                            sha256=hashlib.sha256(b"legacy content\n").hexdigest(),
                        )
                    ],
                )
            ]
        ),
    )

    result = sync_manifest(project)

    assert (target / "team.guidelines.md").read_text(encoding="utf-8") == "current\n"
    assert not legacy.exists()
    assert result.reconciliations[0].removed == [".agents/guidelines/team.guideline.md"]
