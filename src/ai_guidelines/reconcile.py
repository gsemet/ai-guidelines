"""Plan and apply safe, hash-owned guideline file reconciliation.

The reconciliation boundary builds a complete source-to-target map before
writing anything.  It rejects source and target boundary escapes, detects
collisions, and uses same-directory atomic replacement for each managed file.
The consistency model is intentionally best-effort per file rather than a
multi-file transaction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

import ai_guidelines.atomic as atomic
from ai_guidelines.discovery import DiscoveredGuideline
from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.locations import validate_revision
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLockEntry,
)
from ai_guidelines.paths import (
    TargetPathError,
    operation_lock_path,
    resolve_guideline_target,
    resolve_target_path,
    target_is_folder,
)


class ReconciliationError(RuntimeError):
    """Raised when mapping or safe reconciliation cannot proceed."""


class DestinationCollisionError(ReconciliationError):
    """Raised when distinct source files share a destination."""


class UnsafeSourcePathError(ReconciliationError):
    """Raised when a discovered source file leaves its acquired root."""


@dataclass(frozen=True)
class GuidelineDestination:
    """Resolved target path and shape for one discovered guideline."""

    path: Path
    target_root: Path
    folder_target: bool


class MappedGuideline(BaseModel):
    """One validated source file and its project-relative destination."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    source_file: Path
    source_path: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    content: bytes
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DestinationMap(BaseModel):
    """Complete validated destination map produced before any writes."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    files: list[MappedGuideline] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    _files_by_batch_index: dict[int, list[MappedGuideline]] = PrivateAttr(default_factory=dict)
    _warnings_by_batch_index: dict[int, list[str]] = PrivateAttr(default_factory=dict)

    @property
    def destinations(self) -> dict[str, MappedGuideline]:
        """Return project-relative target paths keyed by destination."""
        return {item.target_path: item for item in self.files}

    def for_batch(self, batch_index: int) -> DestinationMap:
        """Return one ordered preflight batch without rereading source content."""
        selected = self._files_by_batch_index.get(batch_index)
        if selected is None:
            raise IndexError("destination map batch index is out of range")
        return DestinationMap(
            files=list(selected),
            warnings=list(self._warnings_by_batch_index.get(batch_index, [])),
        )


class ReconciliationResult(BaseModel):
    """Structured file actions, ownership records, and safety warnings."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    preserved: list[str] = Field(default_factory=list)
    adopted: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    managed_files: list[GuidelineFileRecord] = Field(default_factory=list)
    dry_run: bool = False

    @property
    def actions(self) -> dict[str, list[str]]:
        """Return file actions grouped for reports and callers."""
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
        }


SourceBatch: TypeAlias = (
    tuple[AcquiredSource, Iterable[DiscoveredGuideline]]
    | tuple[AcquiredSource, Iterable[DiscoveredGuideline], str | None]
)


def _safe_relative(value: str, field_name: str) -> str:
    """Validate one source-relative path without accepting host escapes."""
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
    ):
        raise UnsafeSourcePathError(f"{field_name} must remain within its source root")
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts:
        raise UnsafeSourcePathError(f"{field_name} must be a safe relative path")
    return "/".join(parts)


def _suffix_normalized(path: str) -> str:
    """Normalize a supported singular suffix to the plural suffix."""
    if path.endswith(".guideline.md"):
        return f"{path[: -len('.guideline.md')]}.guidelines.md"
    return path


def _source_root(source: AcquiredSource) -> Path:
    """Return the root against which source candidates are checked."""
    return source.path if source.path.is_dir() else source.path.parent


def _assert_source_file_contained(source: AcquiredSource, candidate: Path) -> None:
    """Reject a source file or symlink that resolves outside its root."""
    root = _source_root(source).resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise UnsafeSourcePathError("source guideline could not be resolved safely") from None
    if not resolved.is_relative_to(root):
        raise UnsafeSourcePathError("source guideline escapes its acquired root")


def _assert_target_contained(project_root: Path, candidate: Path) -> None:
    """Reject a target path whose existing symlink chain leaves the project."""
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        raise ReconciliationError("guideline target could not be resolved safely") from None
    if not resolved.is_relative_to(project_root):
        raise ReconciliationError("guideline target escapes the project root")


def _batch_values(batch: Any) -> tuple[AcquiredSource, list[DiscoveredGuideline], str | None]:
    """Unpack one source batch used by multi-source mapping callers."""
    if not isinstance(batch, (tuple, list)) or len(batch) not in (2, 3):
        raise TypeError("source batches must contain a source, files, and optional target")
    source = batch[0]
    files = batch[1]
    target = batch[2] if len(batch) == 3 else None
    if not isinstance(source, AcquiredSource):
        raise TypeError("source batch must start with an AcquiredSource")
    if hasattr(files, "files"):
        files = files.files
    return source, list(files), target


def _iter_batches(
    source: AcquiredSource | Iterable[SourceBatch],
    files: Iterable[DiscoveredGuideline] | None,
    target_path: str | None,
) -> list[tuple[AcquiredSource, list[DiscoveredGuideline], str | None]]:
    """Normalize single-source and multi-source mapping forms."""
    if isinstance(source, AcquiredSource):
        return [(source, list(files or []), target_path)]
    return [_batch_values(batch) for batch in source]


def _target_context(project_root: Path, target_path: str | None) -> tuple[Path, bool]:
    """Resolve one configured target and whether it denotes a folder."""
    if target_path is None:
        return resolve_guideline_target(project_root), True
    try:
        target = resolve_target_path(project_root, target_path)
    except TargetPathError as error:
        raise ReconciliationError(f"configured guideline target is unsafe: {error}") from None
    return target, target_is_folder(target_path, project_root)


def _destination_from_context(source_path: str, target: Path, folder_target: bool) -> Path:
    """Map a safe source-relative path using the flattening policy."""
    normalized_source = _safe_relative(source_path, "source path")
    if folder_target:
        return target / _suffix_normalized(Path(normalized_source).name)
    return target


def resolve_guideline_destination(
    project_root: Path | str,
    source_path: str,
    target_path: str | None = None,
) -> GuidelineDestination:
    """Resolve one discovered source path to a contained project destination.

    Args:
        project_root:
            Consumer project root.
        source_path:
            Safe path relative to the acquired source root.
        target_path:
            Optional project-relative folder or explicit filename.

    Returns:
        The destination path, its target root, and whether it is a folder target.
    """
    root = Path(project_root).expanduser().resolve()
    target, folder_target = _target_context(root, target_path)
    destination = _destination_from_context(source_path, target, folder_target)
    _assert_target_contained(root, destination)
    return GuidelineDestination(
        path=destination,
        target_root=target if folder_target else target.parent,
        folder_target=folder_target,
    )


def _mapped_file(
    project_root: Path,
    source: AcquiredSource,
    candidate: DiscoveredGuideline,
    target: Path,
    folder_target: bool,
) -> MappedGuideline:
    """Validate one candidate, read it once, and calculate its destination hash."""
    source_path = _safe_relative(candidate.source_path, "source path")
    _assert_source_file_contained(source, candidate.path)
    try:
        content = candidate.path.read_bytes()
    except OSError:
        raise ReconciliationError("source guideline could not be read safely") from None
    destination = _destination_from_context(source_path, target, folder_target)
    _assert_target_contained(project_root, destination)
    return MappedGuideline(
        source_file=candidate.path,
        source_path=source_path,
        target_path=destination.relative_to(project_root).as_posix(),
        source_identity=source.location.canonical_source,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _map_batch(
    project_root: Path,
    acquired: AcquiredSource,
    discovered: list[DiscoveredGuideline],
    target_path: str | None,
) -> tuple[list[MappedGuideline], list[str]]:
    """Map one batch and resolve only singular/plural local collisions."""
    target, folder_target = _target_context(project_root, target_path)
    if not folder_target and len(discovered) > 1:
        raise ReconciliationError(
            "a folder source cannot map multiple files to one explicit target filename"
        )
    mapped = [
        _mapped_file(project_root, acquired, candidate, target, folder_target)
        for candidate in discovered
    ]
    by_destination: dict[str, list[MappedGuideline]] = {}
    for item in mapped:
        by_destination.setdefault(item.target_path, []).append(item)
    selected: list[MappedGuideline] = []
    warnings: list[str] = []
    for destination, candidates in by_destination.items():
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        plural = [item for item in candidates if item.source_path.endswith(".guidelines.md")]
        if len(plural) == 1:
            selected.append(plural[0])
            warnings.append(
                f"Skipped a collision from a singular guideline source for {destination}; "
                "the .guidelines.md source was preferred."
            )
        else:
            raise DestinationCollisionError("guideline destination collision detected")
    return selected, warnings


def build_destination_map(
    project_root: Path | str,
    source: AcquiredSource | Iterable[SourceBatch],
    files: Iterable[DiscoveredGuideline] | None = None,
    *,
    target_path: str | None = None,
) -> DestinationMap:
    """Build a complete collision-checked map without writing project state.

    Args:
        project_root:
            Project containment boundary.
        source:
            One acquired source or ``(source, files[, target])`` batches.
        files:
            Discovered files for the single-source form.
        target_path:
            Optional folder or explicit filename for the single source.

    Returns:
        A deterministic destination map with source content and hashes.

    Raises:
        DestinationCollisionError:
            If independent files share a destination.
        ReconciliationError:
            If paths or target mappings are unsafe.
    """
    root = Path(project_root).expanduser().resolve()
    mapped: list[MappedGuideline] = []
    warnings: list[str] = []
    files_by_batch_index: dict[int, list[MappedGuideline]] = {}
    warnings_by_batch_index: dict[int, list[str]] = {}
    for batch_index, (acquired, discovered, batch_target) in enumerate(
        _iter_batches(source, files, target_path)
    ):
        selected, batch_warnings = _map_batch(
            root,
            acquired,
            discovered,
            batch_target,
        )
        mapped.extend(selected)
        warnings.extend(batch_warnings)
        files_by_batch_index[batch_index] = list(selected)
        warnings_by_batch_index[batch_index] = batch_warnings

    destination_sources: dict[str, str] = {}
    for item in mapped:
        previous = destination_sources.get(item.target_path)
        if previous is not None:
            if previous != item.source_identity:
                raise DestinationCollisionError(
                    "distinct guideline sources share a destination collision"
                )
            raise DestinationCollisionError("guideline destination collision detected")
        destination_sources[item.target_path] = item.source_identity
    mapped.sort(key=lambda item: item.target_path)
    result = DestinationMap(files=mapped, warnings=warnings)
    object.__setattr__(result, "_files_by_batch_index", files_by_batch_index)
    object.__setattr__(result, "_warnings_by_batch_index", warnings_by_batch_index)
    return result


def _hash_existing(path: Path) -> str:
    """Hash an existing target file."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ReconciliationError("managed guideline could not be read safely") from None


def _record_by_target(
    lock_entry: GuidelinesLockEntry | None,
    active_target: str,
    folder_target: bool,
) -> dict[str, GuidelineFileRecord]:
    """Index lock records after constraining them to the active target."""
    if lock_entry is None:
        return {}
    target_parts = PurePosixPath(active_target).parts
    records: dict[str, GuidelineFileRecord] = {}
    for record in lock_entry.files:
        normalized_target = record.normalized_target_path
        record_parts = PurePosixPath(normalized_target).parts
        in_scope = (
            len(record_parts) > len(target_parts)
            and record_parts[: len(target_parts)] == target_parts
            if folder_target
            else normalized_target == active_target
        )
        if not in_scope:
            raise ReconciliationError("lock record target is outside the active guideline target")
        records[normalized_target] = record
    return records


def _edited_warning(
    source_path: str,
    target: str,
    expected_hash: str | None,
    current_hash: str,
    *,
    preserved: bool = False,
) -> str:
    """Describe a local edit without exposing file contents."""
    action = "Preserving" if preserved else "Overwriting"
    return (
        f"{action} locally edited guideline: source {source_path}; target {target}; "
        f"ownership hash: expected {expected_hash}, current {current_hash}"
    )


def _safe_existing_target(project_root: Path, target: Path) -> None:
    """Validate an existing target, including a symlink target."""
    if target.exists() or target.is_symlink():
        _assert_target_contained(project_root, target)
        if target.is_dir():
            raise ReconciliationError("guideline target is a directory")


def _validate_precomputed_map(
    project: Path,
    source: AcquiredSource,
    destination_map: DestinationMap,
    effective_target: str | None,
) -> None:
    """Validate a caller-supplied map without rereading source content."""
    destinations: set[str] = set()
    for item in destination_map.files:
        if item.source_identity != source.location.canonical_source:
            raise ReconciliationError("precomputed guideline map has the wrong source identity")
        _assert_source_file_contained(source, item.source_file)
        if hashlib.sha256(item.content).hexdigest() != item.sha256:
            raise ReconciliationError("precomputed guideline map has an invalid content hash")
        expected = resolve_guideline_destination(project, item.source_path, effective_target)
        expected_relative = expected.path.relative_to(project).as_posix()
        if item.target_path != expected_relative:
            raise ReconciliationError("precomputed guideline map has the wrong target")
        if item.target_path in destinations:
            raise DestinationCollisionError("guideline destination collision detected")
        destinations.add(item.target_path)


def _remove_managed_file(path: Path) -> None:
    """Remove one managed file without exposing provider-specific errors."""
    try:
        path.unlink()
    except OSError:
        raise ReconciliationError("managed guideline could not be removed") from None


def reconcile_source(
    project_root: Path | str,
    source: AcquiredSource,
    files: Iterable[DiscoveredGuideline],
    lock_entry: GuidelinesLockEntry | None = None,
    *,
    declaration: GuidelineDeclaration | None = None,
    base_dir: Path | None = None,
    target_path: str | None = None,
    dry_run: bool = False,
    operation_lock_held: bool = False,
    metadata_writer: Callable[[ReconciliationResult], None] | None = None,
    operation_events: list[str] | None = None,
    destination_map: DestinationMap | None = None,
) -> ReconciliationResult:
    """Apply one source map using recorded managed-content hashes.

    Normal operations warn and overwrite desired files whose current content
    differs from the recorded ownership hash.  Stale files are removed only
    when their current content still matches that hash.  ``dry_run`` computes
    the same actions without taking a lock or writing any file.
    """
    project = Path(project_root).expanduser().resolve()
    if declaration is not None:
        try:
            matches = lock_entry is not None and lock_entry.matches(
                declaration,
                base_dir=base_dir,
            )
        except ValueError:
            matches = False
        if not matches:
            raise ReconciliationError("manifest declaration does not match lock identity")
    for revision in (
        source.location.requested_ref,
        lock_entry.requested_ref if lock_entry is not None else None,
        lock_entry.resolved_ref if lock_entry is not None else None,
    ):
        if revision is not None:
            try:
                validate_revision(revision)
            except ValueError:
                raise ReconciliationError("requested source revision is unsafe") from None
    if lock_entry is not None and lock_entry.source != source.location.canonical_source:
        raise ReconciliationError("acquired source does not match lock identity")

    effective_target = target_path
    if effective_target is None and lock_entry is not None:
        effective_target = lock_entry.target_path
    try:
        active_target = (
            resolve_guideline_target(project)
            if effective_target is None
            else resolve_target_path(project, effective_target)
        )
        folder_target = effective_target is None or target_is_folder(effective_target, project)
        active_target_relative = active_target.relative_to(project).as_posix()
    except (TargetPathError, ValueError):
        raise ReconciliationError("configured guideline target is unsafe") from None

    if destination_map is None:
        destination_map = build_destination_map(
            project,
            source,
            list(files),
            target_path=effective_target,
        )
    else:
        _validate_precomputed_map(project, source, destination_map, effective_target)

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []
    unchanged: list[str] = []
    preserved: list[str] = []
    adopted: list[str] = []
    warnings = list(destination_map.warnings)
    if not destination_map.files:
        warnings.append(f"No guideline files matched source {source.location.display_name!r}.")
    records = _record_by_target(lock_entry, active_target_relative, folder_target)
    desired_targets = {item.target_path for item in destination_map.files}
    writes: list[MappedGuideline] = []
    removals: list[Path] = []

    for item in destination_map.files:
        destination = project / Path(item.target_path)
        _safe_existing_target(project, destination)
        previous = records.get(item.target_path)
        if not destination.exists():
            added.append(item.target_path)
            writes.append(item)
        elif previous is None or previous.sha256 is None:
            adopted.append(item.target_path)
            writes.append(item)
        else:
            current_hash = _hash_existing(destination)
            if current_hash != previous.sha256:
                updated.append(item.target_path)
                warnings.append(
                    _edited_warning(
                        item.source_path,
                        item.target_path,
                        previous.sha256,
                        current_hash,
                    )
                )
                writes.append(item)
            elif current_hash == item.sha256:
                unchanged.append(item.target_path)
            else:
                updated.append(item.target_path)
                writes.append(item)

    for target, record in records.items():
        if target in desired_targets:
            continue
        destination = project / Path(target)
        _safe_existing_target(project, destination)
        if not destination.exists():
            continue
        current_hash = _hash_existing(destination)
        if record.sha256 is not None and current_hash == record.sha256:
            removed.append(target)
            removals.append(destination)
        else:
            preserved.append(target)
            warnings.append(
                _edited_warning(
                    record.source_path,
                    target,
                    record.sha256,
                    current_hash,
                    preserved=True,
                )
            )

    for values in (added, updated, removed, unchanged, preserved, adopted):
        values.sort()
    result = ReconciliationResult(
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        preserved=preserved,
        adopted=adopted,
        warnings=warnings,
        managed_files=[
            GuidelineFileRecord(
                source_path=item.source_path,
                target_path=item.target_path,
                sha256=item.sha256,
            )
            for item in destination_map.files
        ],
        dry_run=dry_run,
    )
    if dry_run:
        return result

    def apply_operations() -> None:
        """Apply file actions while the caller owns the operation lock."""
        if operation_events is not None:
            operation_events.append("files")
        for item in writes:
            atomic.atomic_write(project / Path(item.target_path), item.content)
        for destination in removals:
            _remove_managed_file(destination)
        if metadata_writer is not None:
            metadata_writer(result)

    try:
        if operation_lock_held:
            apply_operations()
        else:
            with atomic.advisory_lock(operation_lock_path(project)):
                apply_operations()
    except (OSError, TimeoutError, TargetPathError):
        raise ReconciliationError("guideline reconciliation could not be completed") from None
    return result


map_targets = build_destination_map
plan_reconciliation = build_destination_map
reconcile_guidelines = reconcile_source
