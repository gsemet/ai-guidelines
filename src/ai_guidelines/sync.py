"""Synchronize manifest-declared guidelines at reproducible revisions.

This module composes source parsing, acquisition, discovery, destination
preflight, hash-owned reconciliation, and neutral lockfile publication.  A
normal operation may refresh moving references; a complete lock replays its
recorded revision.  Dry-run and frozen operations never write project files.
"""

from __future__ import annotations

import re
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

import ai_guidelines.atomic as atomic
from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.discovery import DiscoveryResult, discover_guidelines
from ai_guidelines.fetch import AcquiredSource, SourceFetcher, _SourcePathNotFoundError
from ai_guidelines.locations import SourceLocation, parse_location, with_source_path
from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.manifest import load_manifest
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelinesLock,
    GuidelinesLockEntry,
    ReferenceKind,
)
from ai_guidelines.paths import (
    operation_lock_path,
    resolve_guideline_target,
    resolve_target_path,
)
from ai_guidelines.reconcile import ReconciliationResult, build_destination_map, reconcile_source
from ai_guidelines.sparse import selector_sparse_patterns

_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


class SyncError(RuntimeError):
    """Raised when manifest synchronization cannot complete safely."""


class FrozenSyncError(SyncError):
    """Raised when strict frozen replay state is incomplete or unavailable."""


class SyncPlanEntry(BaseModel):
    """Describe one source operation and its project-relative file actions."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    locked_revision: str = "—"
    files: int = 0
    action: str = Field(min_length=1)
    actions: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SyncPlan(BaseModel):
    """Validated synchronization plan shared by execution and reporting."""

    model_config = ConfigDict(extra="forbid")

    target_path: str = Field(min_length=1)
    lock_path: str = Field(min_length=1)
    entries: list[SyncPlanEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False

    @property
    def action_counts(self) -> dict[str, int]:
        """Return aggregate counts for added, updated, removed, and unchanged files."""
        counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
        for entry in self.entries:
            for action, paths in entry.actions.items():
                if action in counts:
                    counts[action] += len(paths)
        return counts


class GuidelinesSyncResult(BaseModel):
    """Result of one synchronization, including prospective lock state."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    plan: SyncPlan
    lockfile: GuidelinesLock
    reconciliations: list[ReconciliationResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False
    lock_written: bool = False

    @property
    def actions(self) -> dict[str, list[str]]:
        """Return sorted paths grouped by reconciliation action."""
        grouped: dict[str, list[str]] = {
            "added": [],
            "updated": [],
            "removed": [],
            "unchanged": [],
        }
        for reconciliation in self.reconciliations:
            for action, paths in reconciliation.actions.items():
                grouped[action].extend(paths)
        for paths in grouped.values():
            paths.sort()
        return grouped

    @property
    def total_files(self) -> int:
        """Return the number of selected source files in the plan."""
        return sum(entry.files for entry in self.plan.entries)

    @property
    def changed_files(self) -> int:
        """Return the number of selected files that would change project state."""
        return sum(len(self.actions[action]) for action in ("added", "updated", "removed"))


@dataclass(frozen=True)
class _PreparedSource:
    """One declaration whose source is acquired and discovered."""

    declaration: GuidelineDeclaration
    location: SourceLocation
    acquired: AcquiredSource
    discovered: DiscoveryResult
    previous: GuidelinesLockEntry | None
    locked: bool
    target_path: str


@dataclass(frozen=True)
class _SynchronizationArtifacts:
    """Results collected while reconciling all prepared sources."""

    reconciliations: list[ReconciliationResult]
    plan_entries: list[SyncPlanEntry]
    warnings: list[str]
    resolved_entries: list[GuidelinesLockEntry]


def _is_semver_constraint(value: str | None) -> bool:
    """Return whether a requested reference is a semantic-version constraint."""
    if value is None:
        return False
    return bool(value) and (value.startswith((">", "<", "=", "~", "^")) or "*" in value)


def _locked_revision(entry: GuidelinesLockEntry) -> str | None:
    """Select the strongest resolved revision from complete lock provenance."""
    return entry.commit or entry.resolved_tag or entry.resolved_version or entry.resolved_ref


def _should_replay_locked(
    declaration: GuidelineDeclaration,
    previous: GuidelinesLockEntry | None,
) -> bool:
    """Return whether ordinary sync should replay the previous exact revision."""
    if previous is None or not previous.is_complete():
        return False
    if previous.source_type == "local":
        return False
    requested_ref = declaration.requested_ref
    if requested_ref is not None and _COMMIT_PATTERN.fullmatch(requested_ref):
        return True
    if previous.semver_constraint is not None:
        return False
    return previous.reference_kind in {"tag", "commit"}


def _reference_kind(value: str | None) -> ReferenceKind | None:
    """Convert persisted cache metadata to the validated source vocabulary."""
    if value in {"branch", "tag", "ambiguous", "commit", "unknown", "local"}:
        return cast(ReferenceKind, value)
    return None


def _load_existing_lock(path: Path) -> GuidelinesLock:
    """Load a neutral lockfile or return an empty document when absent."""
    try:
        return load_lockfile(path)
    except FileNotFoundError:
        return GuidelinesLock()
    except ValueError as error:
        raise SyncError(f"Could not load guideline lockfile: {error}") from None


def _safe_sync_failure(source_name: str, error: Exception | None = None) -> SyncError:
    """Create a source-scoped error without retaining untrusted diagnostics."""
    if isinstance(error, _SourcePathNotFoundError):
        return SyncError(str(error))
    return SyncError(
        f"Could not synchronize guideline source {source_name}: the source operation failed"
    )


def _validate_frozen_state(
    manifest: Any,
    lockfile: GuidelinesLock,
    project_root: Path,
) -> None:
    """Validate all manifest/lock correspondence before frozen resolution."""
    problems: list[str] = []
    matched_entries: set[int] = set()
    for index, declaration in enumerate(manifest.guidelines, start=1):
        try:
            entry = lockfile.find_entry(declaration, base_dir=project_root)
        except ValueError:
            entry = None
        if entry is None:
            problems.append(f"declaration {index} has no matching lock entry")
            continue
        matched_entries.add(id(entry))
        if not entry.matches(declaration, base_dir=project_root):
            problems.append(f"declaration {index} differs from its lock entry")
        elif entry.target_path is None:
            problems.append(f"declaration {index} has no locked target path")
        elif not entry.is_complete():
            problems.append(f"declaration {index} has incomplete lock state")
        elif entry.source_type != "local" and entry.commit is None:
            problems.append(f"declaration {index} has no exact remote commit")
    if len(matched_entries) != len(lockfile.guidelines):
        problems.append("lockfile contains entries not declared in guidelines.yml")
    if problems:
        raise FrozenSyncError("Frozen synchronization failed:\n- " + "\n- ".join(problems))


def _validate_frozen_source_content(
    project: Path,
    acquired: AcquiredSource,
    discovered: DiscoveryResult,
    previous: GuidelinesLockEntry,
    target_path: str,
) -> None:
    """Require current frozen source mappings and hashes to match the lock."""
    try:
        current_map = build_destination_map(
            project,
            acquired,
            discovered.files,
            target_path=target_path,
        )
    except Exception as error:  # noqa: BLE001 - sanitize source-boundary errors
        raise FrozenSyncError(
            f"Frozen synchronization failed: exact source state for "
            f"{previous.name!r} is unavailable"
        ) from error

    expected = sorted(
        (record.source_path, record.normalized_target_path, record.sha256)
        for record in previous.files
    )
    actual = sorted((item.source_path, item.target_path, item.sha256) for item in current_map.files)
    if actual != expected:
        raise FrozenSyncError(
            f"Frozen synchronization failed: source files or content for "
            f"{previous.name!r} differ from locked hashes"
        )


def _effective_target_path(
    project_root: Path,
    declaration: GuidelineDeclaration,
    previous: GuidelinesLockEntry | None,
    default_target_path: str | None = None,
) -> str:
    """Resolve target precedence while retaining a matching lock pin."""
    configured = declaration.normalized_target_path
    if configured is None and previous is not None:
        configured = previous.normalized_target_path
    if configured is None:
        configured = default_target_path
    target = (
        resolve_guideline_target(project_root)
        if configured is None
        else resolve_target_path(project_root, configured)
    )
    return target.relative_to(project_root).as_posix()


def _new_lock_entry(
    declaration: GuidelineDeclaration,
    location: SourceLocation,
    acquired: AcquiredSource,
    reconciliation: ReconciliationResult,
    previous: GuidelinesLockEntry | None,
    *,
    target_path: str,
) -> GuidelinesLockEntry:
    """Create or refresh one lock entry after successful file planning."""
    if previous is not None:
        return previous.model_copy(
            update={"target_path": target_path, "files": reconciliation.managed_files}
        )
    requested_ref = location.requested_ref
    resolved_ref = acquired.resolved_ref
    if location.source_type == "local" and resolved_ref is None:
        resolved_ref = "working-tree"
    captured_at = datetime.now(timezone.utc)
    semver = _is_semver_constraint(requested_ref)
    return GuidelinesLockEntry(
        expression=declaration.source,
        name=declaration.alias or location.display_name,
        source=location.canonical_source,
        source_type=location.source_type,
        requested_ref=requested_ref,
        path=declaration.path,
        paths=declaration.paths,
        resolved_ref=resolved_ref,
        reference_kind=acquired.reference_kind,
        commit=acquired.commit,
        captured_at=captured_at,
        semver_constraint=requested_ref if semver else None,
        resolved_tag=resolved_ref if semver else None,
        resolution_timestamp=captured_at if semver else None,
        pattern=declaration.pattern,
        target_path=target_path,
        alias=declaration.alias,
        files=reconciliation.managed_files,
    )


def _plan_entry(
    declaration: GuidelineDeclaration,
    location: SourceLocation,
    acquired: AcquiredSource,
    reconciliation: ReconciliationResult,
    *,
    locked: bool,
    target_path: str,
) -> SyncPlanEntry:
    """Build the presentation-neutral report entry."""
    return SyncPlanEntry(
        name=declaration.alias or location.display_name,
        source=location.canonical_source,
        target_path=target_path,
        locked_revision=acquired.commit or acquired.resolved_ref or "—",
        files=len(reconciliation.managed_files),
        action="replay locked revision" if locked else "resolve and synchronize",
        actions=reconciliation.actions,
        warnings=list(reconciliation.warnings),
    )


def _materialized_path(root: Path, relative_path: str) -> Path:
    """Return a source path below a materialized snapshot root."""
    return root if relative_path == "." else root.joinpath(*relative_path.split("/"))


def _materialized_location(location: SourceLocation, root: Path) -> tuple[SourceLocation, Path]:
    """Resolve an extensionless cached path as a directory when appropriate."""
    path = _materialized_path(root, location.relative_path)
    if path.is_dir() and location.kind != "folder":
        location = location.model_copy(update={"kind": "folder"})
    return location, path


def _collect_source_contexts(
    project: Path,
    manifest: Any,
    existing_lock: GuidelinesLock,
    *,
    active_fetcher: Any,
    materialized_cache: MaterializedGuidelineCache,
    source_stack: ExitStack,
) -> tuple[
    dict[int, tuple[SourceLocation, GuidelinesLockEntry | None, bool, str]],
    dict[int, AcquiredSource],
    dict[
        tuple[str, str | None],
        list[
            tuple[int, GuidelineDeclaration, SourceLocation, GuidelinesLockEntry | None, bool, str]
        ],
    ],
]:
    """Collect local/cache acquisitions and group remote misses."""
    contexts: dict[int, tuple[SourceLocation, GuidelinesLockEntry | None, bool, str]] = {}
    acquired_sources: dict[int, AcquiredSource] = {}
    pending: dict[
        tuple[str, str | None],
        list[
            tuple[int, GuidelineDeclaration, SourceLocation, GuidelinesLockEntry | None, bool, str]
        ],
    ] = {}
    for index, declaration in enumerate(manifest.guidelines):
        try:
            previous = existing_lock.find_entry(declaration, base_dir=project)
            locked = _should_replay_locked(declaration, previous)
            requested_ref = (
                _locked_revision(previous) if locked and previous is not None else declaration.ref
            )
            location = parse_location(declaration.source, ref=requested_ref, base_dir=project)
            if declaration.path is not None:
                location = with_source_path(location, declaration.path)
            target_path = _effective_target_path(
                project,
                declaration,
                previous,
                manifest.normalized_default_guidelines_path,
            )
            contexts[index] = (location, previous, locked, target_path)
            cached = materialized_cache.lookup(
                location,
                required_paths=declaration.paths
                or ([declaration.path] if declaration.path else None),
                required_pattern=declaration.pattern,
            )
            if cached is not None:
                cached_location, cached_path = _materialized_location(location, cached.root)
                contexts[index] = (cached_location, previous, locked, target_path)
                acquired_sources[index] = AcquiredSource(
                    location=cached_location,
                    root=cached.root,
                    path=cached_path,
                    resolved_ref=cached.resolved_ref,
                    commit=cached.commit,
                    reference_kind=_reference_kind(cached.reference_kind),
                    temporary=False,
                )
            elif location.source_type == "local":
                acquired_sources[index] = source_stack.enter_context(
                    active_fetcher.acquire(location)
                )
            else:
                pending.setdefault((location.repository or "", location.requested_ref), []).append(
                    (index, declaration, location, previous, locked, target_path)
                )
        except (SyncError, FrozenSyncError):
            raise
        except Exception as error:  # noqa: BLE001 - sanitize at the boundary
            raise _safe_sync_failure(declaration.display_name, error) from None
    return contexts, acquired_sources, pending


def _acquire_pending_sources(
    pending: dict[
        tuple[str, str | None],
        list[
            tuple[int, GuidelineDeclaration, SourceLocation, GuidelinesLockEntry | None, bool, str]
        ],
    ],
    acquired_sources: dict[int, AcquiredSource],
    *,
    active_fetcher: Any,
    materialized_cache: MaterializedGuidelineCache,
    source_stack: ExitStack,
    use_cache_checkout: bool,
) -> None:
    """Acquire grouped remote misses and publish materialized snapshots."""
    for group in pending.values():
        locations = [item[2] for item in group]
        patterns = list(
            dict.fromkeys(
                pattern for item in group for pattern in selector_sparse_patterns(item[1])
            )
        )
        try:
            acquire_kwargs: dict[str, Any] = {"sparse_patterns": patterns}
            if use_cache_checkout:
                acquire_kwargs["checkout_root"] = materialized_cache.checkout_path(locations[0])
            try:
                context = active_fetcher.acquire_many(locations, **acquire_kwargs)
            except TypeError as error:
                if "checkout_root" not in str(error):
                    raise
                context = active_fetcher.acquire_many(locations, sparse_patterns=patterns)
            acquired_batch = source_stack.enter_context(context)
            first_acquired = acquired_batch[0]
            if first_acquired.commit is None:
                raise SyncError("remote guideline acquisition did not resolve a commit")
            if use_cache_checkout:
                snapshot = materialized_cache.write_snapshot(
                    locations[0],
                    first_acquired.root,
                    locations,
                    resolved_ref=first_acquired.resolved_ref,
                    commit=first_acquired.commit,
                    reference_kind=first_acquired.reference_kind,
                )
                for item, acquired_item in zip(group, acquired_batch, strict=True):
                    location = item[2]
                    acquired_sources[item[0]] = AcquiredSource(
                        location=location,
                        root=snapshot.root,
                        path=_materialized_path(snapshot.root, location.relative_path),
                        resolved_ref=snapshot.resolved_ref,
                        commit=snapshot.commit,
                        reference_kind=acquired_item.reference_kind,
                        temporary=False,
                    )
            else:
                for item, acquired_item in zip(group, acquired_batch, strict=True):
                    acquired_sources[item[0]] = acquired_item
        except (SyncError, FrozenSyncError):
            raise
        except Exception as error:  # noqa: BLE001 - sanitize at the boundary
            raise _safe_sync_failure(group[0][1].display_name, error) from None


def _prepare_sources(
    project: Path,
    manifest: Any,
    existing_lock: GuidelinesLock,
    *,
    active_fetcher: Any,
    materialized_cache: MaterializedGuidelineCache,
    source_stack: ExitStack,
    use_cache_checkout: bool,
) -> list[_PreparedSource]:
    """Acquire and discover every normal-sync source before mapping."""
    contexts, acquired_sources, pending = _collect_source_contexts(
        project,
        manifest,
        existing_lock,
        active_fetcher=active_fetcher,
        materialized_cache=materialized_cache,
        source_stack=source_stack,
    )
    _acquire_pending_sources(
        pending,
        acquired_sources,
        active_fetcher=active_fetcher,
        materialized_cache=materialized_cache,
        source_stack=source_stack,
        use_cache_checkout=use_cache_checkout,
    )
    prepared: list[_PreparedSource] = []
    for index, declaration in enumerate(manifest.guidelines):
        location, previous, locked, target_path = contexts[index]
        acquired = acquired_sources[index]
        try:
            discovered = discover_guidelines(
                acquired,
                pattern=declaration.pattern,
                paths=declaration.paths,
            )
        except Exception as error:  # noqa: BLE001 - sanitize at the boundary
            raise _safe_sync_failure(declaration.display_name, error) from None
        prepared.append(
            _PreparedSource(
                declaration=declaration,
                location=location,
                acquired=acquired,
                discovered=discovered,
                previous=previous,
                locked=locked,
                target_path=target_path,
            )
        )
    return prepared


def _prepare_frozen_sources(
    project: Path,
    manifest: Any,
    existing_lock: GuidelinesLock,
    *,
    materialized_cache: MaterializedGuidelineCache,
) -> list[_PreparedSource]:
    """Prepare frozen sources using local files or exact cached remote commits."""
    prepared: list[_PreparedSource] = []
    for declaration in manifest.guidelines:
        previous = existing_lock.find_entry(declaration, base_dir=project)
        if previous is None:
            raise FrozenSyncError("Frozen synchronization failed: declaration has no lock entry")
        requested_ref = _locked_revision(previous)
        try:
            location = parse_location(declaration.source, ref=requested_ref, base_dir=project)
            if declaration.path is not None:
                location = with_source_path(location, declaration.path)
            target_path = _effective_target_path(
                project,
                declaration,
                previous,
                manifest.normalized_default_guidelines_path,
            )
            if location.source_type == "local":
                if location.local_path is None or not location.local_path.exists():
                    raise FrozenSyncError(
                        "Frozen synchronization failed: local source for "
                        f"{declaration.display_name!r} "
                        "is unavailable"
                    )
                acquired = AcquiredSource(
                    location=location,
                    root=location.local_path,
                    path=location.local_path,
                    resolved_ref=previous.resolved_ref or "working-tree",
                    commit=previous.commit,
                    reference_kind=previous.reference_kind or "local",
                    temporary=False,
                )
            else:
                if previous.commit is None:
                    raise FrozenSyncError(
                        f"Frozen synchronization failed: exact cache state for "
                        f"{declaration.display_name!r} is unavailable"
                    )
                cached = materialized_cache.lookup(
                    location,
                    touch=False,
                    required_paths=declaration.paths
                    or ([declaration.path] if declaration.path else None),
                    required_pattern=declaration.pattern,
                )
                if cached is None or cached.commit != previous.commit.lower():
                    raise FrozenSyncError(
                        f"Frozen synchronization failed: exact cached revision for "
                        f"{declaration.display_name!r} is unavailable"
                    )
                acquired = AcquiredSource(
                    location=location,
                    root=cached.root,
                    path=_materialized_path(cached.root, location.relative_path),
                    resolved_ref=cached.resolved_ref,
                    commit=cached.commit,
                    reference_kind=_reference_kind(cached.reference_kind),
                    temporary=False,
                )
            discovered = discover_guidelines(
                acquired,
                pattern=declaration.pattern,
                paths=declaration.paths,
            )
            _validate_frozen_source_content(
                project,
                acquired,
                discovered,
                previous,
                target_path,
            )
        except FrozenSyncError:
            raise
        except Exception:
            raise FrozenSyncError(
                f"Frozen synchronization failed: exact source state for "
                f"{declaration.display_name!r} is unavailable"
            ) from None
        prepared.append(
            _PreparedSource(
                declaration=declaration,
                location=location,
                acquired=acquired,
                discovered=discovered,
                previous=previous,
                locked=True,
                target_path=target_path,
            )
        )
    return prepared


def _reconcile_prepared_sources(
    project: Path,
    prepared_sources: list[_PreparedSource],
    destination_map: Any,
    *,
    dry_run: bool,
) -> _SynchronizationArtifacts:
    """Reconcile prepared batches and collect report and lock entries."""
    reconciliations: list[ReconciliationResult] = []
    plan_entries: list[SyncPlanEntry] = []
    warnings: list[str] = []
    resolved_entries: list[GuidelinesLockEntry] = []
    for batch_index, prepared in enumerate(prepared_sources):
        try:
            reconciliation = reconcile_source(
                project,
                prepared.acquired,
                prepared.discovered.files,
                prepared.previous,
                declaration=prepared.declaration if prepared.previous is not None else None,
                base_dir=project,
                target_path=prepared.target_path,
                dry_run=dry_run,
                operation_lock_held=not dry_run,
                destination_map=destination_map.for_batch(batch_index),
            )
            reconciliation.warnings = list(
                dict.fromkeys([*prepared.discovered.warnings, *reconciliation.warnings])
            )
            entry = _new_lock_entry(
                prepared.declaration,
                prepared.location,
                prepared.acquired,
                reconciliation,
                prepared.previous if prepared.locked else None,
                target_path=prepared.target_path,
            )
            resolved_entries.append(entry)
            plan_entries.append(
                _plan_entry(
                    prepared.declaration,
                    prepared.location,
                    prepared.acquired,
                    reconciliation,
                    locked=prepared.locked,
                    target_path=prepared.target_path,
                )
            )
            reconciliations.append(reconciliation)
            warnings.extend(reconciliation.warnings)
        except (SyncError, FrozenSyncError):
            raise
        except Exception:
            raise _safe_sync_failure(prepared.declaration.display_name) from None
    return _SynchronizationArtifacts(reconciliations, plan_entries, warnings, resolved_entries)


def _synchronize_sources(
    project: Path,
    manifest: Any,
    existing_lock: GuidelinesLock,
    source_stack: ExitStack,
    *,
    active_fetcher: Any,
    use_cache_checkout: bool,
    dry_run: bool,
    frozen: bool,
) -> _SynchronizationArtifacts:
    """Prepare, preflight, and reconcile every declaration."""
    materialized_cache = MaterializedGuidelineCache()
    prepared_sources = (
        _prepare_frozen_sources(
            project,
            manifest,
            existing_lock,
            materialized_cache=materialized_cache,
        )
        if frozen
        else _prepare_sources(
            project,
            manifest,
            existing_lock,
            active_fetcher=active_fetcher,
            materialized_cache=materialized_cache,
            source_stack=source_stack,
            use_cache_checkout=use_cache_checkout,
        )
    )
    try:
        destination_map = build_destination_map(
            project,
            [
                (prepared.acquired, prepared.discovered.files, prepared.target_path)
                for prepared in prepared_sources
            ],
        )
    except Exception as error:  # noqa: BLE001 - normalize mapping errors
        if isinstance(error, SyncError):
            raise
        raise SyncError(f"Could not plan guideline destinations: {error}") from None
    return _reconcile_prepared_sources(
        project,
        prepared_sources,
        destination_map,
        dry_run=dry_run or frozen,
    )


def sync_manifest(
    project_root: Path | str,
    *,
    manifest_path: Path | str | None = None,
    lockfile_path: Path | str | None = None,
    frozen: bool = False,
    dry_run: bool = False,
    fetcher: Any | None = None,
) -> GuidelinesSyncResult:
    """Synchronize project guideline declarations at reproducible revisions.

    Args:
        project_root:
            Project containing ``guidelines.yml`` and managed targets.
        manifest_path:
            Optional manifest override.  Defaults to the project root.
        lockfile_path:
            Optional lockfile override.  Defaults to the project root.
        frozen:
            Require complete lock/cache state and perform no writes or
            acquisition.
        dry_run:
            Preview source and file actions without writing project state.
        fetcher:
            Optional injectable acquisition facade for offline tests.

    Returns:
        A synchronization result containing actions, warnings, and lock state.

    Raises:
        FileNotFoundError:
            If the manifest is absent.
        FrozenSyncError:
            If strict replay state is missing or unavailable.
        SyncError:
            If acquisition, discovery, reconciliation, or publication fails.
    """
    project = Path(project_root).expanduser().resolve()
    manifest_file = Path(manifest_path) if manifest_path is not None else project / "guidelines.yml"
    lock_file = (
        Path(lockfile_path) if lockfile_path is not None else project / "guidelines.lock.json"
    )
    active_fetcher = fetcher or SourceFetcher()
    operation_context = (
        nullcontext() if dry_run or frozen else atomic.advisory_lock(operation_lock_path(project))
    )
    with operation_context:
        manifest = load_manifest(manifest_file)
        existing_lock = _load_existing_lock(lock_file)
        if frozen:
            _validate_frozen_state(manifest, existing_lock, project)
        with ExitStack() as source_stack:
            artifacts = _synchronize_sources(
                project,
                manifest,
                existing_lock,
                source_stack,
                active_fetcher=active_fetcher,
                use_cache_checkout=fetcher is None,
                dry_run=dry_run,
                frozen=frozen,
            )
        target = (
            artifacts.plan_entries[0].target_path
            if artifacts.plan_entries
            else resolve_guideline_target(project).relative_to(project).as_posix()
        )
        next_lock = GuidelinesLock(guidelines=artifacts.resolved_entries)
        plan = SyncPlan(
            target_path=target,
            lock_path=str(lock_file),
            entries=artifacts.plan_entries,
            warnings=list(dict.fromkeys(artifacts.warnings)),
            dry_run=dry_run,
        )
        lock_written = False
        if not dry_run and not frozen:
            try:
                save_lockfile(lock_file, next_lock)
            except (OSError, ValueError):
                raise SyncError(
                    "Could not publish guideline lockfile; managed files may already have changed"
                ) from None
            lock_written = True
    return GuidelinesSyncResult(
        plan=plan,
        lockfile=next_lock,
        reconciliations=artifacts.reconciliations,
        warnings=list(dict.fromkeys(artifacts.warnings)),
        dry_run=dry_run,
        lock_written=lock_written,
    )


run_manifest_sync = sync_manifest
run_locked_sync = sync_manifest
synchronize_guidelines = sync_manifest
