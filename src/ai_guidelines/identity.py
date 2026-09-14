"""Immutable declaration identity and deterministic plan fingerprints.

This module is an internal coordination boundary.  It keeps manifest intent
separate from presentation names and from the mutable provenance recorded in a
lockfile, while giving reviewed update plans a stable representation to bind.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from ai_guidelines.locations import SourceLocation
from ai_guidelines.models import GuidelineDeclaration, GuidelinesLock


class DeclarationIdentity:
    """Immutable normalized identity for one manifest declaration.

    The value includes every declaration field that can change acquisition,
    selection, reconciliation, or target placement.  It is intentionally not
    part of the public manifest format.
    """

    __slots__ = (
        "source",
        "source_type",
        "requested_ref",
        "path",
        "paths",
        "pattern",
        "alias",
        "target_path",
    )

    source: str
    source_type: str
    requested_ref: str | None
    path: str | None
    paths: tuple[str, ...] | None
    pattern: str | None
    alias: str | None
    target_path: str

    def __init__(
        self,
        *,
        source: str,
        source_type: str,
        requested_ref: str | None,
        path: str | None,
        paths: Sequence[str] | None,
        pattern: str | None,
        alias: str | None,
        target_path: str,
    ) -> None:
        """Create an immutable normalized declaration identity.

        Args:
            source:
                Canonical source identity.
            source_type:
                Validated local or remote source type.
            requested_ref:
                Requested branch, tag, range, or commit.
            path:
                Optional singular source selector.
            paths:
                Optional plural source selectors.
            pattern:
                Optional suffix-stripped filename pattern.
            alias:
                Optional declaration display alias.
            target_path:
                Effective project-relative destination.
        """
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "requested_ref", requested_ref)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "paths", tuple(paths) if paths is not None else None)
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "target_path", target_path)

    def __setattr__(
        self,
        name: str,
        value: object,
    ) -> None:
        """Reject mutation after construction."""
        raise AttributeError("declaration identity is immutable")

    @classmethod
    def from_declaration(
        cls,
        declaration: GuidelineDeclaration,
        location: SourceLocation,
        *,
        target_path: str,
    ) -> DeclarationIdentity:
        """Build identity from validated declaration and effective target.

        Args:
            declaration:
                Manifest declaration whose intent is being coordinated.
            location:
                Validated location parsed from the declaration.
            target_path:
                Effective project-relative target.

        Returns:
            An immutable normalized identity value.

        Examples:
            >>> from ai_guidelines.locations import parse_location
            >>> declaration = GuidelineDeclaration(
            ...     source="https://example.com/team/repo", alias="team"
            ... )
            >>> identity = DeclarationIdentity.from_declaration(
            ...     declaration,
            ...     parse_location(declaration.source),
            ...     target_path=".github/guidelines",
            ... )
            >>> identity.alias
            'team'
        """
        return cls(
            source=location.canonical_source,
            source_type=location.source_type,
            requested_ref=location.requested_ref,
            path=declaration.path,
            paths=declaration.paths,
            pattern=declaration.pattern,
            alias=declaration.alias,
            target_path=target_path,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible deterministic representation."""
        return {
            "source": self.source,
            "source_type": self.source_type,
            "requested_ref": self.requested_ref,
            "path": self.path,
            "paths": list(self.paths) if self.paths is not None else None,
            "pattern": self.pattern,
            "alias": self.alias,
            "target_path": self.target_path,
        }

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 digest of this identity."""
        return _digest(self.as_dict())


def manifest_fingerprint(
    identities: Sequence[DeclarationIdentity], lockfile: GuidelinesLock
) -> str:
    """Return a deterministic fingerprint for reviewed manifest state.

    Declaration order is significant.  Lock entries are retained in their
    serialized order so reordered, removed, added, or edited state cannot be
    mistaken for the state that produced a reviewed plan.

    Args:
        identities:
            Ordered declaration identities with effective targets.
        lockfile:
            Current lock state relevant to replay and reconciliation.

    Returns:
        A lowercase SHA-256 hexadecimal digest.
    """
    payload = {
        "declarations": [identity.as_dict() for identity in identities],
        "lock": [entry.model_dump(mode="json", exclude_none=True) for entry in lockfile.guidelines],
    }
    return _digest(payload)


def _digest(payload: object) -> str:
    """Hash one JSON-compatible payload with stable key ordering."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
