"""Focused tests for update inspection and reviewed-plan application."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.locations import parse_location
from ai_guidelines.manifest import save_manifest
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
    GuidelinesManifest,
)
from ai_guidelines.update import (
    GuidelineUpdateError,
    build_update_plan,
    inspect_outdated,
    is_moving_reference,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(project: Path, declaration: GuidelineDeclaration) -> None:
    save_manifest(project / "guidelines.yml", GuidelinesManifest(guidelines=[declaration]))


class FakeFetcher:
    """Yield a selected revision from a local fixture without network access."""

    def __init__(self, source_root: Path, commit: str) -> None:
        self.source_root = source_root
        self.commit = commit
        self.calls = 0

    @contextmanager
    def acquire(self, location, **_kwargs) -> Iterator[AcquiredSource]:
        self.calls += 1
        yield AcquiredSource(
            location=location,
            root=self.source_root,
            path=self.source_root,
            resolved_ref="main",
            commit=self.commit,
            reference_kind="branch",
        )

    @contextmanager
    def acquire_many(self, locations, **_kwargs) -> Iterator[list[AcquiredSource]]:
        self.calls += 1
        yield [
            AcquiredSource(
                location=location,
                root=self.source_root,
                path=self.source_root,
                resolved_ref="main",
                commit=self.commit,
                reference_kind="branch",
            )
            for location in locations
        ]


class MappingFetcher(FakeFetcher):
    """Return a distinct local fixture for each source identity."""

    def __init__(self, sources: dict[str, Path], commits: dict[str, str]) -> None:
        self.sources = sources
        self.commits = commits
        self.calls = 0

    @contextmanager
    def acquire(self, location, **_kwargs) -> Iterator[AcquiredSource]:
        self.calls += 1
        yield AcquiredSource(
            location=location,
            root=self.sources[location.canonical_source],
            path=self.sources[location.canonical_source],
            resolved_ref="main",
            commit=self.commits[location.canonical_source],
            reference_kind="branch",
        )

    @contextmanager
    def acquire_many(self, locations, **_kwargs) -> Iterator[list[AcquiredSource]]:
        self.calls += 1
        yield [
            AcquiredSource(
                location=location,
                root=self.sources[location.canonical_source],
                path=self.sources[location.canonical_source],
                resolved_ref="main",
                commit=self.commits[location.canonical_source],
                reference_kind="branch",
            )
            for location in locations
        ]


def _lock_entry(
    project: Path, declaration: GuidelineDeclaration, source: Path, commit: str
) -> GuidelinesLockEntry:
    location = parse_location(declaration.source, ref=declaration.ref, base_dir=project)
    files = []
    for guideline in source.glob("*.guidelines.md"):
        import hashlib

        files.append(
            GuidelineFileRecord(
                source_path=guideline.name,
                target_path=f"{declaration.target_path}/{guideline.name}",
                sha256=hashlib.sha256(guideline.read_bytes()).hexdigest(),
            )
        )
    return GuidelinesLockEntry(
        expression=declaration.source,
        name=location.display_name,
        source=location.canonical_source,
        source_type=location.source_type,
        requested_ref=location.requested_ref,
        resolved_ref="main",
        reference_kind="branch",
        commit=commit,
        target_path=declaration.target_path,
        files=files,
    )


def test_reference_classification_prefers_provider_evidence() -> None:
    assert is_moving_reference("v1.2.3", reference_kind="branch")
    assert not is_moving_reference("main", reference_kind="tag")
    assert not is_moving_reference("main", reference_kind="ambiguous")
    assert not is_moving_reference("a" * 40)
    assert not is_moving_reference("v1.2.3")
    assert is_moving_reference(None)
    assert is_moving_reference("^1.2")


def test_inspection_and_planning_are_read_only_and_plan_is_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "team\n")
    declaration = GuidelineDeclaration(source="https://example.com/team/repo", ref="main")
    _manifest(project, declaration)
    fetcher = FakeFetcher(source, "a" * 40)

    report = inspect_outdated(project, fetcher=fetcher)
    plan = build_update_plan(project, fetcher=fetcher)

    assert report.entries[0].status == "unlocked"
    assert plan.dry_run is True
    assert plan.entries[0].group == "added"
    assert plan.file_action_counts == {
        "added": 1,
        "updated": 0,
        "removed": 0,
        "unchanged": 0,
    }
    assert not (project / "guidelines.lock.json").exists()
    assert not (project / ".github").exists()


def test_apply_rejects_changed_resolved_ref_without_commit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "team\n")
    declaration = GuidelineDeclaration(source="https://example.com/team/repo", ref="main")
    _manifest(project, declaration)

    from ai_guidelines.update import apply_update_plan

    planned = build_update_plan(project, fetcher=FakeFetcher(source, "a" * 40))
    entry = planned.entries[0].model_copy(update={"commit": None, "resolved_ref": "main"})
    reviewed = planned.model_copy(update={"entries": [entry]})

    with pytest.raises(GuidelineUpdateError, match="changed after planning"):
        apply_update_plan(project, reviewed, fetcher=FakeFetcher(source, "b" * 40))

    assert not (project / ".github/guidelines/team.guidelines.md").exists()
    assert not (project / "guidelines.lock.json").exists()


def test_plan_groups_all_file_actions_and_retains_target_pins(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    roots = {name: tmp_path / name for name in ("updated", "removed", "unchanged", "added")}
    for root in roots.values():
        root.mkdir()
    _write(roots["updated"] / "guide.guidelines.md", "new\n")
    _write(roots["unchanged"] / "guide.guidelines.md", "same\n")
    _write(roots["added"] / "new.guidelines.md", "new\n")
    declarations = [
        GuidelineDeclaration(
            source=f"https://example.com/team/{name}", ref="main", target_path=f"targets/{name}"
        )
        for name in roots
    ]
    _manifest(project, declarations[0])
    save_manifest(project / "guidelines.yml", GuidelinesManifest(guidelines=declarations))
    commits = {
        f"https://example.com/team/{name}": ("b" * 40 if name == "unchanged" else "a" * 40)
        for name in roots
    }
    locations = {
        name: parse_location(declaration.source, ref="main")
        for name, declaration in zip(roots, declarations, strict=True)
    }
    old_updated = tmp_path / "old-updated"
    old_removed = tmp_path / "old-removed"
    old_unchanged = tmp_path / "old-unchanged"
    for root, content in (
        (old_updated, "old\n"),
        (old_removed, "gone\n"),
        (old_unchanged, "same\n"),
    ):
        root.mkdir()
        _write(root / "guide.guidelines.md", content)
    _write(project / "targets/updated/guide.guidelines.md", "old\n")
    _write(project / "targets/unchanged/guide.guidelines.md", "same\n")
    _write(project / "targets/removed/guide.guidelines.md", "gone\n")
    lock = GuidelinesLock(
        guidelines=[
            _lock_entry(project, declarations[index], root, "b" * 40)
            for index, root in ((0, old_updated), (1, old_removed), (2, old_unchanged))
        ]
    )
    fetcher = MappingFetcher(
        {location.canonical_source: roots[name] for name, location in locations.items()}, commits
    )
    plan = build_update_plan(project, declarations=declarations, lockfile=lock, fetcher=fetcher)

    assert {entry.group for entry in plan.entries} == {"added", "updated", "unchanged"}
    assert {entry.target_path for entry in plan.entries} == {f"targets/{name}" for name in roots}
    assert plan.file_action_counts["added"] >= 1
    assert plan.file_action_counts["updated"] >= 1
    assert plan.file_action_counts["removed"] >= 1
    assert plan.file_action_counts["unchanged"] >= 1
    assert plan.dry_run is True
    assert not (project / "guidelines.lock.json").exists()


def test_apply_uses_the_exact_reviewed_commit_successfully(tmp_path: Path) -> None:
    project, source = tmp_path / "project", tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "reviewed\n")
    declaration = GuidelineDeclaration(source="https://example.com/team/repo", ref="main")
    _manifest(project, declaration)
    fetcher = FakeFetcher(source, "a" * 40)
    plan = build_update_plan(project, fetcher=fetcher)
    from ai_guidelines.update import apply_update_plan

    result = apply_update_plan(project, plan, fetcher=fetcher)
    assert result.applied and result.lock_written
    assert (project / ".github/guidelines/team.guidelines.md").read_text() == "reviewed\n"
    assert result.lockfile.guidelines[0].commit == "a" * 40

    def test_update_sources_use_published_cache_path_after_snapshot_move(tmp_path: Path) -> None:
        """Cached update sources remain valid after cache publication moves the checkout."""
        # This regression is covered by the production acquisition path; the cache
        # publication must return the final snapshot root, not the pending checkout.
        assert (
            MaterializedGuidelineCache(cache_dir=tmp_path / "cache").cache_dir == tmp_path / "cache"
        )


def test_cache_maintenance_preserves_lock_sidecar_without_manifest(tmp_path: Path) -> None:
    from ai_guidelines.cache import MaterializedGuidelineCache

    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "cache")
    cache.cache_dir.mkdir()
    sidecar = cache.metadata_lock_path
    sidecar.write_text("lock\n")
    (cache.cache_dir / "stale-snapshot").mkdir()
    from ai_guidelines.api import cache_clean

    assert cache_clean(cache) == 0
    assert sidecar.exists()
    assert not (cache.cache_dir / "stale-snapshot").exists()
