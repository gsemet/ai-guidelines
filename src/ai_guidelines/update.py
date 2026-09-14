"""Read-only update inspection and reviewed-plan application services."""

from __future__ import annotations

import re
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_guidelines import atomic
from ai_guidelines.discovery import discover_guidelines
from ai_guidelines.fetch import ReferenceKind, SourceFetcher
from ai_guidelines.locations import parse_location, with_source_path
from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.manifest import load_manifest
from ai_guidelines.models import GuidelineDeclaration, GuidelinesLock, GuidelinesLockEntry
from ai_guidelines.paths import operation_lock_path
from ai_guidelines.reconcile import ReconciliationResult, reconcile_source
from ai_guidelines.update_acquisition import acquire_update_sources

_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SEMVER = re.compile(r"^v?(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){0,2}$")
_MOVING = (">", "<", "=", "~", "^")


class GuidelineUpdateError(RuntimeError):
    """Raised when an update cannot be safely inspected or applied."""


class OutdatedEntry(BaseModel):
    """Revision state for one declaration."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    current_revision: str = "—"
    available_revision: str = "—"
    status: Literal["outdated", "up-to-date", "pinned", "unlocked", "local"]

    @property
    def update_available(self) -> bool:
        return self.status == "outdated"


class OutdatedReport(BaseModel):
    """Read-only report of configured source revisions."""

    model_config = ConfigDict(extra="forbid")
    entries: list[OutdatedEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def updates_available(self) -> list[OutdatedEntry]:
        return [entry for entry in self.entries if entry.update_available]


class UpdatePlanEntry(BaseModel):
    """One source and its file-level update actions."""

    model_config = ConfigDict(extra="forbid")
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

    @property
    def group(self) -> str:
        return "unchanged" if self.status == "pinned" else self.status

    @property
    def acquisition_revision(self) -> str | None:
        return (
            self.commit
            or self.resolved_ref
            or (self.available_revision if self.available_revision != "—" else None)
        )


class UpdatePlan(BaseModel):
    """Complete no-write update plan."""

    model_config = ConfigDict(extra="forbid")
    entries: list[UpdatePlanEntry] = Field(default_factory=list)
    # Planning always performs a dry run: no project or cache state is written.
    dry_run: bool = True

    @property
    def groups(self) -> dict[str, list[UpdatePlanEntry]]:
        groups: dict[str, list[UpdatePlanEntry]] = {
            name: [] for name in ("added", "updated", "removed", "unchanged")
        }
        for entry in self.entries:
            groups[entry.group].append(entry)
        return groups

    @property
    def action_counts(self) -> dict[str, int]:
        return {name: len(entries) for name, entries in self.groups.items()}

    @property
    def file_action_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(("added", "updated", "removed", "unchanged"), 0)
        for entry in self.entries:
            for action, paths in entry.actions.items():
                if action in counts:
                    counts[action] += len(paths)
        return counts

    @property
    def has_changes(self) -> bool:
        return any(self.groups[name] for name in ("added", "updated", "removed"))


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
    if reference_kind in {"tag", "commit", "ambiguous", "unknown"}:
        return False
    if reference_kind == "branch":
        return True
    if reference is None:
        return True
    value = reference.strip()
    return not (_SHA.fullmatch(value) or _SEMVER.fullmatch(value))


def _revision(entry: GuidelinesLockEntry | None) -> str:
    return "—" if entry is None else (entry.commit or entry.resolved_ref or "—")


def _inputs(
    project: Path, declarations: list[GuidelineDeclaration] | None, lockfile: GuidelinesLock | None
) -> tuple[list[GuidelineDeclaration], GuidelinesLock, str | None]:
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
    location = parse_location(declaration.source, ref=declaration.ref, base_dir=project)
    return with_source_path(location, declaration.path) if declaration.path else location


def _discovery_paths(declaration: GuidelineDeclaration) -> list[str] | None:
    """Return plural selectors without duplicating an already-acquired path."""
    return declaration.paths


def _target(
    project: Path,
    declaration: GuidelineDeclaration,
    previous: GuidelinesLockEntry | None,
    default: str | None,
) -> str:
    from ai_guidelines.paths import resolve_guideline_target, resolve_target_path

    configured = (
        declaration.normalized_target_path
        or (previous.normalized_target_path if previous else None)
        or default
    )
    target = (
        resolve_guideline_target(project)
        if configured is None
        else resolve_target_path(project, configured)
    )
    return target.relative_to(project).as_posix()


def inspect_outdated(
    project_root: Path | str,
    *,
    declarations: list[GuidelineDeclaration] | None = None,
    lockfile: GuidelinesLock | None = None,
    fetcher: Any | None = None,
) -> OutdatedReport:
    """Inspect revisions without writing the project or cache."""
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
    """Build all source and file actions without mutation."""
    project = Path(project_root).expanduser().resolve()
    declarations, lock, default = _inputs(project, declarations, lockfile)
    active = fetcher or SourceFetcher()
    requests: list[tuple[int, GuidelineDeclaration, Any]] = []
    contexts: dict[int, tuple[Any, Any, Any, str]] = {}
    entries: dict[int, UpdatePlanEntry] = {}
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
            )
    return UpdatePlan(entries=[entries[index] for index in range(len(declarations))])


def apply_update_plan(
    project_root: Path | str, plan: UpdatePlan, *, fetcher: Any | None = None
) -> GuidelineUpdateResult:
    """Apply exactly the immutable revisions recorded in a reviewed plan."""
    project = Path(project_root).expanduser().resolve()
    declarations, existing, default = _inputs(project, None, None)
    if not plan.has_changes:
        return GuidelineUpdateResult(plan=plan, lockfile=existing)
    active = fetcher or SourceFetcher()
    requests = []
    for index, declaration in enumerate(declarations):
        planned = plan.entries[index]
        if planned.group != "unchanged":
            location = _location(declaration, project)
            if planned.acquisition_revision:
                location = location.model_copy(
                    update={"requested_ref": planned.acquisition_revision}
                )
            requests.append((index, declaration, location))
    with atomic.advisory_lock(operation_lock_path(project)), ExitStack() as stack:
        acquired = acquire_update_sources(requests, fetcher=active, source_stack=stack)
        next_entries = list(existing.guidelines)
        reconciliations = []
        for index, declaration, location in requests:
            planned, source = plan.entries[index], acquired[index]
            requested_ref = declaration.requested_ref
            reviewed_resolution = planned.commit or planned.resolved_ref
            actual_resolution = source.commit or source.resolved_ref
            if reviewed_resolution is not None and actual_resolution != reviewed_resolution:
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
            )
            reconciliations.append(result)
            entry = GuidelinesLockEntry(
                expression=declaration.source,
                name=declaration.alias or location.display_name,
                source=location.canonical_source,
                source_type=location.source_type,
                requested_ref=requested_ref,
                resolved_ref=source.resolved_ref
                or ("working-tree" if location.source_type == "local" else None),
                reference_kind=source.reference_kind,
                commit=source.commit,
                target_path=target,
                captured_at=datetime.now(timezone.utc),
                files=result.managed_files,
            )
            if previous:
                next_entries = [entry if item is previous else item for item in next_entries]
            else:
                next_entries.append(entry)
        next_lock = GuidelinesLock(guidelines=next_entries)
        save_lockfile(project / "guidelines.lock.json", next_lock)
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
