"""Read-only update inspection and reviewed-plan application services."""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal, NoReturn, SupportsIndex

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_guidelines import atomic
from ai_guidelines.discovery import discover_guidelines
from ai_guidelines.fetch import ReferenceKind, SourceFetcher
from ai_guidelines.identity import DeclarationIdentity, manifest_fingerprint
from ai_guidelines.locations import replace_source_location
from ai_guidelines.lock_entries import build_lock_entry
from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.manifest import load_manifest
from ai_guidelines.models import GuidelineDeclaration, GuidelinesLock, GuidelinesLockEntry
from ai_guidelines.paths import declaration_location, operation_lock_path, select_guideline_target
from ai_guidelines.reconcile import ReconciliationJournal, ReconciliationResult, reconcile_source
from ai_guidelines.semver import is_range
from ai_guidelines.update_acquisition import acquire_update_sources

_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SEMVER = re.compile(r"^v?(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){0,2}$")
_MOVING = (">", "<", "=", "~", "^")


class GuidelineUpdateError(RuntimeError):
    """Raised when an update cannot be safely inspected or applied.

    .. versionchanged:: 0.2.0
        Applying a plan now rejects changed declaration identity, lock state,
        resolution, or source type before publication.
    """


class _FrozenList(list[Any]):
    """List-compatible container that rejects mutation after plan creation."""

    def _reject(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> NoReturn:
        """Reject an operation that would mutate a reviewed plan."""
        raise TypeError("reviewed update plans are immutable")

    append = _reject
    clear = _reject
    extend = _reject
    insert = _reject
    pop = _reject
    remove = _reject
    reverse = _reject
    sort = _reject

    def __delitem__(self, _key: object) -> NoReturn:
        """Reject deletion from the frozen list."""
        self._reject()

    def __iadd__(self, _value: Iterable[Any]) -> list[Any]:  # type: ignore[override, misc]
        """Reject in-place list concatenation."""
        self._reject()

    def __imul__(self, _value: SupportsIndex) -> list[Any]:  # type: ignore[override]
        """Reject in-place list repetition."""
        self._reject()

    def __setitem__(
        self,
        _key: object,
        _value: object,
    ) -> NoReturn:
        """Reject item replacement in the frozen list."""
        self._reject()


class _FrozenDict(dict[str, Any]):
    """Dict-compatible container that rejects mutation after plan creation."""

    def _reject(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> NoReturn:
        """Reject an operation that would mutate a reviewed plan."""
        raise TypeError("reviewed update plans are immutable")

    clear = _reject
    pop = _reject
    popitem = _reject
    setdefault = _reject
    update = _reject

    def __delitem__(self, _key: object) -> NoReturn:
        """Reject deletion from the frozen dictionary."""
        self._reject()

    def __ior__(self, _value: Any, /) -> _FrozenDict:  # type: ignore[override, misc]
        """Reject in-place dictionary merging."""
        self._reject()

    def __setitem__(
        self,
        _key: object,
        _value: object,
    ) -> NoReturn:
        """Reject item replacement in the frozen dictionary."""
        self._reject()


class OutdatedEntry(BaseModel):
    """Revision state for one declaration.

    The status distinguishes moving sources, exact pins, local sources, and
    declarations that have not yet been locked.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    current_revision: str = "—"
    available_revision: str = "—"
    status: Literal["outdated", "up-to-date", "pinned", "unlocked", "local"]

    @property
    def update_available(self) -> bool:
        """Return whether acquisition found a newer source revision."""
        return self.status == "outdated"


class OutdatedReport(BaseModel):
    """Read-only report of configured source revisions."""

    model_config = ConfigDict(extra="forbid")
    entries: list[OutdatedEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def updates_available(self) -> list[OutdatedEntry]:
        """Return only entries whose moving source has changed."""
        return [entry for entry in self.entries if entry.update_available]


class UpdatePlanEntry(BaseModel):
    """One source and its file-level update actions.

    The entry captures the exact acquisition revision and declaration identity
    that a later application must verify.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    current_revision: str = "—"
    available_revision: str = "—"
    requested_ref: str | None = None
    resolved_ref: str | None = None
    commit: str | None = None
    reference_kind: ReferenceKind | None = None
    status: Literal["added", "updated", "removed", "unchanged", "pinned"]
    target_path: str = Field(min_length=1)
    files: int = 0
    actions: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    declaration_fingerprint: str = ""

    @field_validator("actions", mode="after")
    @classmethod
    def freeze_actions(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """Freeze nested action paths without changing their list API."""
        return _FrozenDict({name: _FrozenList(paths) for name, paths in value.items()})

    @field_validator("warnings", mode="after")
    @classmethod
    def freeze_warnings(cls, value: list[str]) -> list[str]:
        """Freeze nested plan warnings without changing their list API."""
        return _FrozenList(value)

    @property
    def group(self) -> str:
        """Return the report group used for a plan entry."""
        return "unchanged" if self.status == "pinned" else self.status

    @property
    def acquisition_revision(self) -> str | None:
        """Return the strongest exact revision available for acquisition."""
        return (
            self.commit
            or self.resolved_ref
            or (self.available_revision if self.available_revision != "—" else None)
        )


class UpdatePlan(BaseModel):
    """Complete no-write update plan.

    .. versionchanged:: 0.2.0
        Reviewed plans are immutable and bind declaration, manifest, and lock
        fingerprints before application.  Plans also identify stale lock entries
        that need consolidation even when source files are unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: list[UpdatePlanEntry] = Field(default_factory=list)
    manifest_fingerprint: str = ""
    lockfile_needs_rewrite: bool = False
    # Planning always performs a dry run: no project or cache state is written.
    dry_run: bool = True

    @field_validator("entries", mode="after")
    @classmethod
    def freeze_entries(cls, value: list[UpdatePlanEntry]) -> list[UpdatePlanEntry]:
        """Freeze the ordered plan entries at the review boundary."""
        return _FrozenList(value)

    @property
    def fingerprint(self) -> str:
        """Return the reviewed manifest/declaration/lock fingerprint."""
        return self.manifest_fingerprint

    @property
    def groups(self) -> dict[str, list[UpdatePlanEntry]]:
        """Return entries grouped into added, updated, removed, and unchanged."""
        groups: dict[str, list[UpdatePlanEntry]] = {
            name: [] for name in ("added", "updated", "removed", "unchanged")
        }
        for entry in self.entries:
            groups[entry.group].append(entry)
        return groups

    @property
    def action_counts(self) -> dict[str, int]:
        """Return counts of source-level entries in each plan group."""
        return {name: len(entries) for name, entries in self.groups.items()}

    @property
    def file_action_counts(self) -> dict[str, int]:
        """Return counts of file-level actions across all plan entries."""
        counts = dict.fromkeys(("added", "updated", "removed", "unchanged"), 0)
        for entry in self.entries:
            for action, paths in entry.actions.items():
                if action in counts:
                    counts[action] += len(paths)
        return counts

    @property
    def has_changes(self) -> bool:
        """Return whether applying the plan would change project files."""
        return any(self.groups[name] for name in ("added", "updated", "removed"))

    @property
    def requires_application(self) -> bool:
        """Return whether source changes or lockfile consolidation require application."""
        return self.has_changes or self.lockfile_needs_rewrite


class GuidelineUpdateResult(BaseModel):
    """Result of applying an update plan."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    plan: UpdatePlan
    lockfile: GuidelinesLock
    reconciliations: list[ReconciliationResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = False
    applied: bool = False
    lock_written: bool = False


def is_moving_reference(
    reference: str | None, *, reference_kind: ReferenceKind | None = None
) -> bool:
    """Classify a reference, preferring provider evidence over its spelling."""
    if reference_kind == "local":
        return True
    if reference is not None and is_range(reference):
        return True
    if reference_kind in {"tag", "commit", "ambiguous", "unknown"}:
        return False
    if reference_kind == "branch":
        return True
    if reference is None:
        return True
    value = reference.strip()
    return not (_SHA.fullmatch(value) or _SEMVER.fullmatch(value))


def _revision(entry: GuidelinesLockEntry | None) -> str:
    """Return the display revision for a lock entry."""
    return "—" if entry is None else (entry.commit or entry.resolved_ref or "—")


def _inputs(
    project: Path,
    declarations: list[GuidelineDeclaration] | None,
    lockfile: GuidelinesLock | None,
) -> tuple[list[GuidelineDeclaration], GuidelinesLock, str | None]:
    """Load or normalize declarations, lock state, and the default target."""
    default = None
    if declarations is None:
        manifest = load_manifest(project / "guidelines.yml")
        declarations, default = manifest.guidelines, manifest.normalized_default_guidelines_path
    for declaration in declarations:
        declaration.bind_source_base(project)
    if lockfile is None:
        lockfile = (
            load_lockfile(project / "guidelines.lock.json")
            if (project / "guidelines.lock.json").exists()
            else GuidelinesLock()
        )
    return declarations, lockfile, default


def _location(declaration: GuidelineDeclaration, project: Path) -> Any:
    """Resolve a declaration into its provider-neutral source location."""
    return declaration_location(declaration, project)


def _discovery_paths(declaration: GuidelineDeclaration) -> list[str] | None:
    """Return plural selectors without duplicating an already-acquired path."""
    return declaration.paths


def _target(
    project: Path,
    declaration: GuidelineDeclaration,
    previous: GuidelinesLockEntry | None,
    default: str | None,
) -> str:
    """Resolve and normalize the effective target directory for a declaration."""
    target = select_guideline_target(
        project,
        declaration=declaration,
        lock_entry=previous,
        default_target_path=default,
    )
    return target.relative_to(project).as_posix()


def _matching_entries(
    project: Path,
    declarations: list[GuidelineDeclaration],
    lockfile: GuidelinesLock,
) -> list[GuidelinesLockEntry | None]:
    """Return the existing lock entry selected for each manifest declaration."""
    return [lockfile.find_entry(declaration, base_dir=project) for declaration in declarations]


def _identities(
    project: Path,
    declarations: list[GuidelineDeclaration],
    lockfile: GuidelinesLock,
    default: str | None,
) -> list[DeclarationIdentity]:
    """Build ordered declaration identities with effective targets."""
    identities: list[DeclarationIdentity] = []
    for declaration in declarations:
        location = _location(declaration, project)
        previous = lockfile.find_entry(declaration, base_dir=project)
        identities.append(
            DeclarationIdentity.from_declaration(
                declaration,
                location,
                target_path=_target(project, declaration, previous, default),
            )
        )
    return identities


def inspect_outdated(
    project_root: Path | str,
    *,
    declarations: list[GuidelineDeclaration] | None = None,
    lockfile: GuidelinesLock | None = None,
    fetcher: Any | None = None,
) -> OutdatedReport:
    """Inspect revisions without writing the project or cache.

    .. versionchanged:: 0.2.0
        Reports distinguish provider-resolved revisions from current lock state.

    Args:
        project_root:
            Project containing the manifest and optional lockfile.
        declarations:
            Optional declarations to inspect instead of loading the manifest.
        lockfile:
            Optional lock state to inspect instead of loading the lockfile.
        fetcher:
            Optional injectable acquisition facade for tests or offline use.

    Returns:
        A report describing current and available revisions.

    Raises:
        GuidelineUpdateError:
            If an update source cannot be inspected safely.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     project = Path(directory)
        ...     _ = (project / "guidelines.yml").write_text(
        ...         "guidelines: []\\n", encoding="utf-8"
        ...     )
        ...     inspect_outdated(project).entries
        []
    """
    project = Path(project_root).expanduser().resolve()
    declarations, lock, _ = _inputs(project, declarations, lockfile)
    active = fetcher or SourceFetcher()
    entries: list[OutdatedEntry] = []
    with ExitStack() as stack:
        for declaration in declarations:
            location = _location(declaration, project)
            previous = lock.find_entry(declaration, base_dir=project)
            name, source = declaration.alias or location.display_name, location.canonical_source
            if location.source_type == "local":
                entries.append(
                    OutdatedEntry(
                        name=name,
                        source=source,
                        current_revision=_revision(previous),
                        available_revision=_revision(previous),
                        status="local",
                    )
                )
                continue
            if previous and not is_moving_reference(
                declaration.ref, reference_kind=previous.reference_kind
            ):
                entries.append(
                    OutdatedEntry(
                        name=name,
                        source=source,
                        current_revision=_revision(previous),
                        available_revision=_revision(previous),
                        status="pinned",
                    )
                )
                continue
            acquired = stack.enter_context(active.acquire(location))
            available = acquired.commit or acquired.resolved_ref or "—"
            status: Literal["outdated", "up-to-date", "pinned", "unlocked", "local"] = (
                "unlocked"
                if previous is None
                else ("outdated" if previous.commit != acquired.commit else "up-to-date")
            )
            entries.append(
                OutdatedEntry(
                    name=name,
                    source=source,
                    current_revision=_revision(previous),
                    available_revision=available,
                    status=status,
                )
            )
    return OutdatedReport(entries=entries)


def build_update_plan(
    project_root: Path | str,
    *,
    declarations: list[GuidelineDeclaration] | None = None,
    lockfile: GuidelinesLock | None = None,
    fetcher: Any | None = None,
) -> UpdatePlan:
    """Build all source and file actions without mutation.

    .. versionchanged:: 0.2.0
        The plan is immutable and binds declaration, manifest, and lock fingerprints.

    Args:
        project_root:
            Project containing the manifest and optional lockfile.
        declarations:
            Optional declarations to plan instead of loading the manifest.
        lockfile:
            Optional lock state to plan against instead of loading the lockfile.
        fetcher:
            Optional injectable acquisition facade for tests or offline use.

    Returns:
        An immutable plan bound to the current manifest and lock state.

    Raises:
        GuidelineUpdateError:
            If a source cannot be inspected or its actions cannot be planned.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     project = Path(directory)
        ...     _ = (project / "guidelines.yml").write_text(
        ...         "guidelines: []\\n", encoding="utf-8"
        ...     )
        ...     build_update_plan(project).entries
        []
    """
    project = Path(project_root).expanduser().resolve()
    declarations, lock, default = _inputs(project, declarations, lockfile)
    active = fetcher or SourceFetcher()
    requests: list[tuple[int, GuidelineDeclaration, Any]] = []
    contexts: dict[int, tuple[Any, Any, Any, str]] = {}
    entries: dict[int, UpdatePlanEntry] = {}
    identities = _identities(project, declarations, lock, default)
    matching_entries = _matching_entries(project, declarations, lock)
    lockfile_needs_rewrite = [
        entry for entry in matching_entries if entry is not None
    ] != lock.guidelines
    for index, declaration in enumerate(declarations):
        location, previous = (
            _location(declaration, project),
            lock.find_entry(declaration, base_dir=project),
        )
        target = _target(project, declaration, previous, default)
        contexts[index] = (location, previous, declaration, target)
        if previous and not is_moving_reference(
            declaration.ref, reference_kind=previous.reference_kind
        ):
            entries[index] = UpdatePlanEntry(
                name=declaration.alias or location.display_name,
                source=location.canonical_source,
                current_revision=_revision(previous),
                available_revision=_revision(previous),
                requested_ref=location.requested_ref,
                commit=previous.commit,
                resolved_ref=previous.resolved_ref,
                reference_kind=previous.reference_kind,
                status="pinned",
                target_path=target,
                declaration_fingerprint=identities[index].fingerprint,
            )
        else:
            requests.append((index, declaration, location))
    with ExitStack() as stack:
        acquired = acquire_update_sources(requests, fetcher=active, source_stack=stack)
        for index, declaration, location in requests:
            source = acquired[index]
            discovered = discover_guidelines(
                source,
                pattern=declaration.pattern,
                paths=_discovery_paths(declaration),
            )
            previous = contexts[index][1]
            reconciliation = reconcile_source(
                project,
                source,
                discovered.files,
                previous,
                declaration=declaration if previous else None,
                base_dir=project,
                target_path=contexts[index][3],
                dry_run=True,
            )
            changed = (
                previous is None
                or previous.commit != source.commit
                or any(reconciliation.actions[a] for a in ("added", "updated", "removed"))
            )
            status: Literal["added", "updated", "removed", "unchanged", "pinned"] = (
                "added" if previous is None else ("updated" if changed else "unchanged")
            )
            entries[index] = UpdatePlanEntry(
                name=declaration.alias or location.display_name,
                source=location.canonical_source,
                current_revision=_revision(previous),
                available_revision=source.commit or source.resolved_ref or "—",
                requested_ref=location.requested_ref,
                resolved_ref=source.resolved_ref,
                commit=source.commit,
                reference_kind=source.reference_kind,
                status=status,
                target_path=contexts[index][3],
                files=len(discovered.files),
                actions=reconciliation.actions,
                warnings=discovered.warnings + reconciliation.warnings,
                declaration_fingerprint=identities[index].fingerprint,
            )
    ordered_entries = [entries[index] for index in range(len(declarations))]
    return UpdatePlan(
        entries=ordered_entries,
        manifest_fingerprint=manifest_fingerprint(identities, lock),
        lockfile_needs_rewrite=lockfile_needs_rewrite,
    )


def apply_update_plan(
    project_root: Path | str,
    plan: UpdatePlan,
    *,
    fetcher: Any | None = None,
) -> GuidelineUpdateResult:
    """Apply exactly the immutable revisions recorded in a reviewed plan.

    .. versionchanged:: 0.2.0
        Application rejects stale reviewed plans and verifies the reviewed
        resolution before writing files or lock state.

    Args:
        project_root:
            Project containing the manifest and managed guideline targets.
        plan:
            Reviewed immutable plan produced by :func:`build_update_plan`.
        fetcher:
            Optional injectable acquisition facade for tests or offline use.

    Returns:
        The applied plan, resulting lockfile, and reconciliation actions.

    Raises:
        GuidelineUpdateError:
            If the plan is stale or acquisition resolves a different source.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     project = Path(directory)
        ...     _ = (project / "guidelines.yml").write_text(
        ...         "guidelines: []\\n", encoding="utf-8"
        ...     )
        ...     plan = build_update_plan(project)
        ...     apply_update_plan(project, plan).applied
        False
    """
    project = Path(project_root).expanduser().resolve()
    active = fetcher or SourceFetcher()
    journal = ReconciliationJournal(project)
    try:
        with atomic.advisory_lock(operation_lock_path(project)), ExitStack() as stack:
            declarations, existing, default = _inputs(project, None, None)
            identities = _identities(project, declarations, existing, default)
            current_fingerprint = manifest_fingerprint(identities, existing)
            if not plan.manifest_fingerprint or plan.manifest_fingerprint != current_fingerprint:
                raise GuidelineUpdateError(
                    "reviewed update plan is stale; the manifest or lockfile "
                    "changed; rerun the update"
                )
            if len(plan.entries) != len(declarations):
                raise GuidelineUpdateError(
                    "reviewed update plan is stale; declaration count changed; rerun the update"
                )
            planned_locations: dict[int, Any] = {}
            for index, declaration in enumerate(declarations):
                planned = plan.entries[index]
                if (
                    planned.declaration_fingerprint
                    and planned.declaration_fingerprint != identities[index].fingerprint
                ):
                    raise GuidelineUpdateError(
                        f"reviewed update plan is stale for declaration {index + 1}; "
                        "rerun the update"
                    )
                location = _location(declaration, project)
                previous = existing.find_entry(declaration, base_dir=project)
                target = _target(project, declaration, previous, default)
                if planned.source != location.canonical_source or planned.target_path != target:
                    raise GuidelineUpdateError(
                        f"reviewed update plan targets a different declaration {index + 1}; "
                        "rerun the update"
                    )
                planned_locations[index] = location
            matching_entries = _matching_entries(project, declarations, existing)
            lockfile_needs_rewrite = [
                entry for entry in matching_entries if entry is not None
            ] != existing.guidelines
            if not plan.has_changes and not lockfile_needs_rewrite:
                return GuidelineUpdateResult(plan=plan, lockfile=existing)
            requests = []
            for index, declaration in enumerate(declarations):
                planned = plan.entries[index]
                if planned.group != "unchanged":
                    location = planned_locations[index]
                    if planned.acquisition_revision:
                        location = replace_source_location(
                            location,
                            requested_ref=planned.acquisition_revision,
                        )
                    requests.append((index, declaration, location))
            journal.capture([project / "guidelines.lock.json"])
            acquired = (
                acquire_update_sources(requests, fetcher=active, source_stack=stack)
                if requests
                else {}
            )
            current_declarations, current_existing, current_default = _inputs(project, None, None)
            current_identities = _identities(
                project, current_declarations, current_existing, current_default
            )
            if manifest_fingerprint(current_identities, current_existing) != current_fingerprint:
                raise GuidelineUpdateError(
                    "reviewed update plan became stale during acquisition; rerun the update"
                )
            next_entries_by_index = {
                index: entry for index, entry in enumerate(matching_entries) if entry is not None
            }
            reconciliations = []
            for index, declaration, _source_location in requests:
                planned, source = plan.entries[index], acquired[index]
                reviewed_resolution = planned.commit or planned.resolved_ref
                actual_resolution = source.commit or source.resolved_ref
                if reviewed_resolution is not None and actual_resolution != reviewed_resolution:
                    raise GuidelineUpdateError(
                        f"Guideline source {planned.name} changed after planning; rerun the update"
                    )
                if source.location.source_type != planned_locations[index].source_type:
                    raise GuidelineUpdateError(
                        f"Guideline source {planned.name} changed after planning; rerun the update"
                    )
                previous = existing.find_entry(declaration, base_dir=project)
                discovered = discover_guidelines(
                    source,
                    pattern=declaration.pattern,
                    paths=_discovery_paths(declaration),
                )
                target = _target(project, declaration, previous, default)
                result = reconcile_source(
                    project,
                    source,
                    discovered.files,
                    previous,
                    declaration=declaration if previous else None,
                    base_dir=project,
                    target_path=target,
                    operation_lock_held=True,
                    journal=journal,
                )
                reconciliations.append(result)
                entry = build_lock_entry(
                    declaration,
                    planned_locations[index],
                    source,
                    managed_files=result.managed_files,
                    target_path=target,
                    previous=previous,
                )
                next_entries_by_index[index] = entry
            next_entries = [
                next_entries_by_index[index]
                for index in range(len(declarations))
                if index in next_entries_by_index
            ]
            next_lock = GuidelinesLock(guidelines=next_entries)
            save_lockfile(project / "guidelines.lock.json", next_lock)
        journal.commit()
    except Exception:
        journal.rollback()
        raise
    return GuidelineUpdateResult(
        plan=plan,
        lockfile=next_lock,
        reconciliations=reconciliations,
        applied=True,
        lock_written=True,
    )


plan_updates = build_update_plan
inspect_guideline_updates = inspect_outdated
apply_updates = apply_update_plan
