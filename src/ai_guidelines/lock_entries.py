"""Canonical construction of validated guideline lock entries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.locations import SourceLocation
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLockEntry,
)
from ai_guidelines.semver import is_range


def build_lock_entry(
    declaration: GuidelineDeclaration,
    location: SourceLocation,
    acquired: AcquiredSource,
    managed_files: Sequence[GuidelineFileRecord],
    *,
    target_path: str,
    previous: GuidelinesLockEntry | None = None,
    preserve_previous_provenance: bool = False,
) -> GuidelinesLockEntry:
    """Build one validated lock entry for sync and reviewed updates.

    .. versionchanged:: 0.2.0
        Lock construction retains all declaration selectors, aliases, targets,
        and semantic-version provenance in one canonical path.

    Args:
        declaration:
            Current manifest declaration.
        location:
            Validated, declaration-facing source location.
        acquired:
            Source acquisition result containing resolved provenance.
        managed_files:
            Fresh source-to-target ownership records.
        target_path:
            Effective project-relative target.
        previous:
            Matching entry whose exact provenance may be retained.
        preserve_previous_provenance:
            Retain exact replay metadata instead of refreshing moving-source
            provenance.

    Returns:
        A freshly validated lock entry with complete declaration identity.

    Raises:
        ValueError:
            If the supplied identity or provenance is inconsistent.

    Examples:
        >>> from pathlib import Path
        >>> from ai_guidelines.locations import parse_location
        >>> declaration = GuidelineDeclaration(
        ...     source="https://example.com/team/repo", ref="main"
        ... )
        >>> location = parse_location(declaration.source, ref=declaration.ref)
        >>> acquired = AcquiredSource(
        ...     location=location,
        ...     root=Path("/tmp/repo"),
        ...     path=Path("/tmp/repo"),
        ...     resolved_ref="main",
        ...     commit="a" * 40,
        ...     reference_kind="branch",
        ... )
        >>> build_lock_entry(
        ...     declaration,
        ...     location,
        ...     acquired,
        ...     [],
        ...     target_path=".github/guidelines",
        ... ).is_complete()
        True
    """
    captured_at = datetime.now(timezone.utc)
    if preserve_previous_provenance:
        if previous is None or not previous.matches(
            declaration, base_dir=declaration.source_base_dir
        ):
            raise ValueError("previous lock entry does not match the declaration")
        payload = previous.model_dump(exclude_none=True)
        payload.update(
            _identity_fields(declaration, location, target_path),
            files=[record.model_dump(exclude_none=True) for record in managed_files],
        )
        return GuidelinesLockEntry.model_validate(payload)

    requested_ref = location.requested_ref
    resolved_ref = acquired.resolved_ref
    if location.source_type == "local" and resolved_ref is None:
        resolved_ref = "working-tree"
    semver = is_range(requested_ref)
    return GuidelinesLockEntry.model_validate(
        {
            **_identity_fields(declaration, location, target_path),
            "requested_ref": requested_ref,
            "resolved_ref": resolved_ref,
            "reference_kind": acquired.reference_kind,
            "commit": acquired.commit,
            "captured_at": captured_at,
            "semver_constraint": requested_ref if semver else None,
            "resolved_tag": resolved_ref if semver else None,
            "resolution_timestamp": captured_at if semver else None,
            "files": list(managed_files),
        }
    )


def _identity_fields(
    declaration: GuidelineDeclaration,
    location: SourceLocation,
    target_path: str,
) -> dict[str, object]:
    """Return the declaration identity fields shared by every lock entry."""
    return {
        "expression": declaration.source,
        "name": declaration.alias or location.display_name,
        "source": location.canonical_source,
        "source_type": location.source_type,
        "path": declaration.path,
        "paths": declaration.paths,
        "pattern": declaration.pattern,
        "target_path": target_path,
        "alias": declaration.alias,
    }
