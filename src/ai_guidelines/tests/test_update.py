"""Focused tests for update inspection and reviewed-plan application."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.fetch import AcquiredSource, SourceFetcher
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
from ai_guidelines.update_acquisition import acquire_update_sources


def _write(path: Path, content: str) -> None:
    """Write a UTF-8 update fixture and create its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _manifest(project: Path, declaration: GuidelineDeclaration) -> None:
    """Persist one declaration as the project manifest fixture."""
    save_manifest(project / "guidelines.yml", GuidelinesManifest(guidelines=[declaration]))


class FakeFetcher:
    """Yield a selected revision from a local fixture without network access."""

    def __init__(
        self,
        source_root: Path,
        commit: str,
    ) -> None:
        """Initialize the fixture root, revision, and call counter."""
        self.source_root = source_root
        self.commit = commit
        self.calls = 0

    @contextmanager
    def acquire(
        self,
        location,
        **_kwargs,
    ) -> Iterator[AcquiredSource]:
        """Yield one deterministic acquired source."""
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
    def acquire_many(
        self,
        locations,
        **_kwargs,
    ) -> Iterator[list[AcquiredSource]]:
        """Yield deterministic acquired sources for a batch."""
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

    def __init__(
        self,
        sources: dict[str, Path],
        commits: dict[str, str],
    ) -> None:
        """Initialize source and commit mappings for multiple declarations."""
        self.sources = sources
        self.commits = commits
        self.calls = 0

    @contextmanager
    def acquire(
        self,
        location,
        **_kwargs,
    ) -> Iterator[AcquiredSource]:
        """Yield the fixture mapped to one source identity."""
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
    def acquire_many(
        self,
        locations,
        **_kwargs,
    ) -> Iterator[list[AcquiredSource]]:
        """Yield mapped fixtures for a batch of source locations."""
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
    project: Path,
    declaration: GuidelineDeclaration,
    source: Path,
    commit: str,
) -> GuidelinesLockEntry:
    """Build a complete lock entry for an existing source fixture."""
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
    """Prefer provider reference metadata over spelling heuristics."""
    assert is_moving_reference("v1.2.3", reference_kind="branch")
    assert not is_moving_reference("main", reference_kind="tag")
    assert not is_moving_reference("main", reference_kind="ambiguous")
    assert not is_moving_reference("a" * 40)
    assert not is_moving_reference("v1.2.3")
    assert is_moving_reference(None)
    assert is_moving_reference("^1.2")


def test_persisted_semver_constraint_remains_moving_when_tag_classified(
    tmp_path: Path,
) -> None:
    """Treat a persisted semantic-version range as moving despite tag evidence."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "team\n")
    declaration = GuidelineDeclaration(
        source="https://example.com/team/repo",
        ref="^1.2.3",
        target_path=".github/guidelines",
    )
    _manifest(project, declaration)
    previous = _lock_entry(project, declaration, source, "b" * 40).model_copy(
        update={
            "reference_kind": "tag",
            "resolved_ref": "v1.2.3",
            "semver_constraint": "^1.2.3",
            "resolved_tag": "v1.2.3",
            "resolution_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )
    lockfile = GuidelinesLock(guidelines=[previous])

    report = inspect_outdated(
        project,
        lockfile=lockfile,
        fetcher=FakeFetcher(source, "a" * 40),
    )
    plan = build_update_plan(
        project,
        lockfile=lockfile,
        fetcher=FakeFetcher(source, "a" * 40),
    )

    assert report.entries[0].status == "outdated"
    assert plan.entries[0].group == "updated"


def test_inspection_and_planning_are_read_only_and_plan_is_dry_run(tmp_path: Path) -> None:
    """Keep outdated inspection and update planning write-free."""
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


def test_reviewed_plan_nested_collections_are_immutable(tmp_path: Path) -> None:
    """A caller cannot alter reviewed entries or file actions in place."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "team\n")
    _manifest(project, GuidelineDeclaration(source="https://example.com/team/repo", ref="main"))

    plan = build_update_plan(project, fetcher=FakeFetcher(source, "a" * 40))

    with pytest.raises(TypeError, match="immutable"):
        plan.entries.append(plan.entries[0])
    with pytest.raises(TypeError, match="immutable"):
        plan.entries[0].actions["added"].append("unexpected")


def test_apply_rejects_changed_resolved_ref_without_commit(tmp_path: Path) -> None:
    """Reject a reviewed plan whose exact resolved revision has changed."""
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


def test_apply_rejects_manifest_mutation_during_acquisition(tmp_path: Path) -> None:
    """Reject a plan that becomes stale while its reviewed source is acquired."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "team\n")
    original_declaration = GuidelineDeclaration(source="https://example.com/team/repo", ref="main")
    _manifest(project, original_declaration)
    plan = build_update_plan(project, fetcher=FakeFetcher(source, "a" * 40))
    acquisition_started = threading.Event()
    release_acquisition = threading.Event()

    class BlockingFetcher(FakeFetcher):
        """Pause acquisition until the test has changed the manifest."""

        @contextmanager
        def acquire_many(self, locations, **kwargs) -> Iterator[list[AcquiredSource]]:
            """Signal acquisition and yield the deterministic fixture afterward."""
            acquisition_started.set()
            assert release_acquisition.wait(timeout=5)
            with super().acquire_many(locations, **kwargs) as acquired:
                yield acquired

    errors: list[BaseException] = []

    def apply() -> None:
        """Apply the reviewed plan in a worker thread."""
        try:
            from ai_guidelines.update import apply_update_plan

            apply_update_plan(
                project,
                plan,
                fetcher=BlockingFetcher(source, "a" * 40),
            )
        except BaseException as error:  # pragma: no cover - assertion reports details
            errors.append(error)

    worker = threading.Thread(target=apply)
    worker.start()
    assert acquisition_started.wait(timeout=5)
    _manifest(
        project,
        GuidelineDeclaration(source="https://example.com/team/repo", ref="other"),
    )
    release_acquisition.set()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], GuidelineUpdateError)
    assert "during acquisition" in str(errors[0])
    assert not (project / ".github/guidelines/team.guidelines.md").exists()
    assert not (project / "guidelines.lock.json").exists()


def test_plan_groups_all_file_actions_and_retains_target_pins(tmp_path: Path) -> None:
    """Group all source and file actions while retaining target pins."""
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
    """Apply the exact reviewed commit and publish its managed file."""
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
    assert (project / ".guidelines-operation.lock").exists()
    assert not (project / ".ai-guidelines-operation.lock").exists()


def test_apply_preserves_unpinned_source_identity(tmp_path: Path) -> None:
    """Preserve an unpinned declaration identity across update application."""
    project, source = tmp_path / "project", tmp_path / "source"
    project.mkdir()
    _write(source / "team.guidelines.md", "unpinned\n")
    declaration = GuidelineDeclaration(source="https://example.com/team/repo")
    _manifest(project, declaration)
    fetcher = FakeFetcher(source, "a" * 40)

    from ai_guidelines.update import apply_update_plan

    first_plan = build_update_plan(project, fetcher=fetcher)
    first_result = apply_update_plan(project, first_plan, fetcher=fetcher)
    second_plan = build_update_plan(project, fetcher=fetcher)

    assert first_result.lockfile.guidelines[0].requested_ref is None
    assert len(first_result.lockfile.guidelines) == 1
    assert second_plan.entries[0].group == "unchanged"


def test_update_sources_use_published_cache_path_after_snapshot_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached update sources point at the final root after snapshot promotion."""

    class CachedFetcher(SourceFetcher):
        """Create a source in the cache-owned pending checkout."""

        @contextmanager
        def acquire_many(
            self,
            locations,
            **kwargs,
        ):
            """Yield a source rooted in the cache-owned pending checkout."""
            checkout_root = kwargs["checkout_root"]
            location = locations[0]
            source_path = checkout_root / Path(location.relative_path)
            _write(source_path, "cached\n")
            yield [
                AcquiredSource(
                    location=location,
                    root=checkout_root,
                    path=source_path,
                    resolved_ref="main",
                    commit="a" * 40,
                    reference_kind="branch",
                )
            ]

    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(
        "ai_guidelines.update_acquisition.MaterializedGuidelineCache", lambda: cache
    )
    location = parse_location("https://example.com/team/repo#main:guidelines/team.guideline.md")
    declaration = GuidelineDeclaration(source=location.expression)

    with ExitStack() as stack:
        acquired = acquire_update_sources(
            [(0, declaration, location)],
            fetcher=CachedFetcher(),
            source_stack=stack,
        )

    source = acquired[0]
    expected_root = cache.cache_path(location, "a" * 40)
    assert source.root == expected_root
    assert source.path == expected_root / location.relative_path
    assert source.path.read_text(encoding="utf-8") == "cached\n"
    assert not list(cache.cache_dir.glob("*_pending_*"))


def test_update_sources_reuse_complete_cached_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse a complete materialized snapshot without acquiring the source again."""
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "cache")
    location = parse_location("https://example.com/team/repo#main:guidelines/team.guideline.md")
    checkout = tmp_path / "checkout"
    _write(checkout / location.relative_path, "cached\n")
    cache.write_snapshot(
        location,
        checkout,
        [location],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )

    class CacheHitFetcher(SourceFetcher):
        """Fail if a complete cached source is unnecessarily acquired."""

        def acquire_many(self, *_args, **_kwargs):
            """Make an unexpected cache miss fail the test immediately."""
            raise AssertionError("cache hit attempted acquisition")

    monkeypatch.setattr(
        "ai_guidelines.update_acquisition.MaterializedGuidelineCache", lambda: cache
    )
    declaration = GuidelineDeclaration(source=location.expression)
    with ExitStack() as stack:
        acquired = acquire_update_sources(
            [(0, declaration, location)],
            fetcher=CacheHitFetcher(),
            source_stack=stack,
        )

    source = acquired[0]
    expected_root = cache.cache_path(location, "a" * 40)
    assert source.root == expected_root
    assert source.path == expected_root / location.relative_path
    assert source.path.read_text(encoding="utf-8") == "cached\n"
    assert source.temporary is False


def test_update_sources_retry_legacy_fetcher_without_checkout_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry compatible fetchers that predate the checkout-root argument."""
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(
        "ai_guidelines.update_acquisition.MaterializedGuidelineCache", lambda: cache
    )
    location = parse_location("https://example.com/team/repo#main:guidelines/team.guideline.md")
    source_root = tmp_path / "source"
    source_path = source_root / location.relative_path
    _write(source_path, "legacy\n")

    class LegacyFetcher(SourceFetcher):
        """Expose the historical acquire-many signature."""

        def __init__(self) -> None:
            self.calls = 0

        @contextmanager
        def acquire_many(self, locations, *, sparse_patterns=None):
            """Yield sources using the historical acquisition signature."""
            self.calls += 1
            assert sparse_patterns == []
            yield [
                AcquiredSource(
                    location=location,
                    root=source_root,
                    path=source_path,
                    resolved_ref="main",
                    commit=None,
                    reference_kind="branch",
                )
                for location in locations
            ]

    fetcher = LegacyFetcher()
    declaration = GuidelineDeclaration(source=location.expression)
    with ExitStack() as stack:
        acquired = acquire_update_sources(
            [(0, declaration, location)],
            fetcher=fetcher,
            source_stack=stack,
        )

    assert fetcher.calls == 1
    assert acquired[0].path == source_path
    assert acquired[0].path.read_text(encoding="utf-8") == "legacy\n"


def test_cache_maintenance_preserves_lock_sidecar_without_manifest(tmp_path: Path) -> None:
    """Clean cache contents without deleting the metadata lock sidecar."""
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
