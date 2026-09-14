"""Validated manifest and neutral lockfile contracts.

The models in this module describe user intent, resolved source provenance,
and managed-file ownership. They deliberately do not contain source fetching,
reconciliation, reporting, or agent-host compatibility models.

Examples:
    >>> manifest = GuidelinesManifest(guidelines=["./shared/guidelines/"])
    >>> manifest.guidelines[0].source
    './shared/guidelines/'
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from ai_guidelines._validation import (
    PATH_METACHARACTERS as _PATH_METACHARACTERS,
)
from ai_guidelines._validation import has_control_characters, validate_revision
from ai_guidelines.locations import parse_location

SourceType = Literal["gitlab", "github", "git", "local"]
ReferenceKind = Literal["branch", "tag", "ambiguous", "commit", "unknown", "local"]

_ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for generated records."""
    return datetime.now(timezone.utc)


def _validate_revision(value: str) -> str:
    """Validate a revision without echoing unsafe command input."""
    return validate_revision(value, error_type=ValueError)


def _source_details(
    source: str, base_dir: Path | None = None
) -> tuple[str, SourceType, str | None, str]:
    """Resolve one source expression without acquiring or executing its content."""
    if has_control_characters(source):
        raise ValueError("source must contain printable characters")
    value = source.strip()
    if not value:
        raise ValueError("source must not be empty")
    try:
        location = parse_location(value, base_dir=base_dir)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return (
        location.canonical_source,
        location.source_type,
        location.requested_ref,
        location.display_name,
    )


def _validate_pattern_value(value: str | None) -> str | None:
    """Validate a filename-stem pattern without allowing directory traversal."""
    if value is None:
        return None
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError("pattern must be a non-empty filename glob")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("pattern must contain printable characters")
    return value


def _validate_source_path(value: str | None, field_name: str = "path") -> str | None:
    """Validate one repository-relative source path."""
    if value is None:
        return None
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        raise ValueError(f"{field_name} must be a non-empty relative source path")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{field_name} must contain printable characters")
    if any(character in _PATH_METACHARACTERS for character in normalized):
        raise ValueError(f"{field_name} must be a literal path")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} must be a safe relative source path")
    if any(part.startswith(("#", "-")) for part in parts):
        raise ValueError(f"{field_name} components must be literal and safe")
    if PureWindowsPath(normalized).drive or PureWindowsPath(normalized).is_absolute():
        raise ValueError(f"{field_name} must be a safe relative source path")
    return "/".join(parts)


def _validate_selector(value: str) -> str:
    """Validate a repository-relative selector that may contain glob syntax."""
    if not isinstance(value, str):
        raise TypeError("paths entries must be strings")
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        raise ValueError("paths entries must be non-empty relative source paths")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("paths entries must contain printable characters")
    if any(part in {"", ".", ".."} for part in PurePosixPath(normalized).parts):
        raise ValueError("paths entries must be safe relative source paths")
    if PureWindowsPath(normalized).drive or PureWindowsPath(normalized).is_absolute():
        raise ValueError("paths entries must be safe relative source paths")
    return "/".join(PurePosixPath(normalized).parts)


def _validate_alias(value: str | None) -> str | None:
    """Validate an identifier safe for display and command lookup."""
    if value is None:
        return None
    if not _ALIAS_PATTERN.fullmatch(value):
        raise ValueError("alias must match ^[a-zA-Z0-9._-]+$")
    return value


def _validate_relative_path(value: str, field_name: str) -> str:
    """Validate a project-relative POSIX path on every host platform."""
    if not value or "\x00" in value:
        raise ValueError(f"{field_name} must be a non-empty path")
    normalized = value.replace("\\", "/")
    windows_path = PureWindowsPath(normalized)
    if (
        normalized.startswith("/")
        or PurePosixPath(normalized).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ValueError(f"{field_name} must be relative to the project")
    if ".." in PurePosixPath(normalized).parts:
        raise ValueError(f"{field_name} must not contain '..' path components")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{field_name} must contain printable characters")
    return value


def _normalize_target(value: str) -> str:
    """Return a stable POSIX target representation."""
    return "/".join(part for part in value.replace("\\", "/").split("/") if part)


def _as_utc(value: datetime, field_name: str) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(timezone.utc)


class SourceIdentity(BaseModel):
    """Stable source identity separated from its display name and alias.

    .. versionchanged:: 0.2.0
        Source identity now uses credential-free canonical source values.

    Args:
        canonical_source:
            URL or absolute local identity used for comparisons.
        display_name:
            Human-readable source name.
        alias:
            Optional safe identifier chosen by the project.

    Raises:
        pydantic.ValidationError: If identity data is empty or unsafe.

    Examples:
        >>> SourceIdentity(
        ...     canonical_source="https://example.com/guidelines", display_name="guidelines"
        ... ).display_name
        'guidelines'
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    canonical_source: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    alias: str | None = None

    @field_validator("canonical_source")
    @classmethod
    def validate_canonical_source(cls, value: str) -> str:
        """Normalize and validate the durable identity."""
        canonical, _, _, _ = _source_details(value)
        return canonical

    @field_validator("alias")
    @classmethod
    def validate_identity_alias(cls, value: str | None) -> str | None:
        """Apply the public alias grammar."""
        return _validate_alias(value)


class GuidelineDeclaration(BaseModel):
    """One user-authored manifest declaration.

    .. versionchanged:: 0.2.0
        Declarations support plural selectors, aliases, and validated project-relative targets.

    Args:
        source:
            Local or remote source expression.
        ref:
            Optional branch, tag, commit, or version constraint.
        path:
            Legacy literal source-relative path.
        pattern:
            Legacy filename-stem glob.
        paths:
            Plural source selectors.
        target_path:
            Optional project-relative destination.
        alias:
            Optional stable command identifier.

    Raises:
        pydantic.ValidationError: If source, selectors, revisions, or targets are unsafe.

    Examples:
        >>> GuidelineDeclaration(source="github/example/guidelines/", alias="team").alias
        'team'
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True, hide_input_in_errors=True)

    source: str = Field(min_length=1)
    ref: str | None = None
    path: str | None = None
    pattern: str | None = None
    paths: list[str] | None = None
    target_path: str | None = None
    alias: str | None = None
    _source_base_dir: Path = PrivateAttr(default_factory=Path.cwd)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """Validate source syntax and credential safety."""
        _source_details(value)
        return value.strip()

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        """Validate an optional revision before it reaches Git."""
        return _validate_revision(value) if value is not None else None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        """Validate a legacy literal source path."""
        return _validate_source_path(value)

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str | None) -> str | None:
        """Validate a legacy filename pattern."""
        return _validate_pattern_value(value)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: list[str] | None) -> list[str] | None:
        """Validate plural source selectors."""
        if value is None:
            return None
        if not value:
            raise ValueError("paths must contain at least one selector")
        return [_validate_selector(item) for item in value]

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, value: str | None) -> str | None:
        """Validate a project-relative target."""
        return _validate_relative_path(value, "target_path") if value is not None else None

    @field_validator("alias")
    @classmethod
    def validate_declaration_alias(cls, value: str | None) -> str | None:
        """Validate the stable declaration alias."""
        return _validate_alias(value)

    @model_validator(mode="after")
    def validate_selector_exclusivity(self) -> GuidelineDeclaration:
        """Reject ambiguous mixtures of legacy and plural selectors."""
        if self.paths is not None and (self.path is not None or self.pattern is not None):
            raise ValueError("paths cannot be combined with path or pattern")
        return self

    def bind_source_base(self, base_dir: Path | str) -> GuidelineDeclaration:
        """Bind relative local identity to a project directory.

        Args:
            base_dir:
                Directory used to resolve relative local sources.

        Returns:
            This declaration, for convenient fluent setup.

        Raises:
            OSError: If the base directory cannot be resolved.

        Examples:
            >>> GuidelineDeclaration(source="./guidelines/").bind_source_base(
            ...     "/tmp/project"
            ... ).source_base_dir.name
            'project'
        """
        self._source_base_dir = Path(base_dir).expanduser().resolve()
        return self

    @property
    def source_base_dir(self) -> Path:
        """Return the directory used for relative source identity."""
        return self._source_base_dir

    @property
    def canonical_source(self) -> str:
        """Return the normalized durable source identity."""
        canonical, _, _, _ = _source_details(self.source, self._source_base_dir)
        if self.path is not None:
            canonical = f"{canonical}/{self.path}"
        return canonical

    def canonical_source_for(self, base_dir: Path | None = None) -> str:
        """Return identity resolved against an explicit project base."""
        canonical, _, _, _ = _source_details(
            self.source, base_dir if base_dir is not None else self._source_base_dir
        )
        return f"{canonical}/{self.path}" if self.path is not None else canonical

    @property
    def requested_ref(self) -> str | None:
        """Return the explicit revision or one encoded by the source expression."""
        return self.ref or _source_details(self.source, self._source_base_dir)[2]

    @property
    def display_name(self) -> str:
        """Return a suffix-stripped human-readable source name."""
        if self.path:
            value = PurePosixPath(self.path).name
        else:
            _, _, _, value = _source_details(self.source, self._source_base_dir)
            if not value:
                value = self.canonical_source.rstrip("/").rsplit("/", 1)[-1]
        for suffix in (".guidelines.md", ".guideline.md"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                break
        return value.removesuffix(".git") or "guidelines"

    @property
    def identity(self) -> SourceIdentity:
        """Return canonical, display, and alias metadata."""
        return SourceIdentity(
            canonical_source=self.canonical_source,
            display_name=self.display_name,
            alias=self.alias,
        )

    def identity_for(self, base_dir: Path | None = None) -> SourceIdentity:
        """Return identity metadata using an explicit project base."""
        return SourceIdentity(
            canonical_source=self.canonical_source_for(base_dir),
            display_name=self.display_name,
            alias=self.alias,
        )

    @property
    def selected_paths(self) -> list[str] | None:
        """Return plural selectors, or ``None`` for legacy selectors."""
        return list(self.paths) if self.paths is not None else None

    @property
    def normalized_target_path(self) -> str | None:
        """Return the target with stable POSIX separators."""
        return _normalize_target(self.target_path) if self.target_path else None


class GuidelinesManifest(BaseModel):
    """Version-one project-owned ``guidelines.yml`` document.

    .. versionchanged:: 0.2.0
        Manifest declarations are normalized and bound to a project source base.

    Args:
        version:
            Supported manifest schema version, currently ``1``.
        default_guidelines_path:
            Optional project-relative target fallback.
        guidelines:
            Ordered scalar or object declarations.

    Raises:
        pydantic.ValidationError: If version, structure, or declarations are invalid.

    Examples:
        >>> GuidelinesManifest(guidelines=["./shared/guidelines/"]).version
        1
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Literal[1] = 1
    default_guidelines_path: str | None = None
    guidelines: list[GuidelineDeclaration] = Field(default_factory=list)
    _source_base_dir: Path = PrivateAttr(default_factory=Path.cwd)
    _source_base_bound: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_scalar_entries(cls, value: Any) -> Any:
        """Normalize scalar declarations into object declarations."""
        if not isinstance(value, dict) or not isinstance(value.get("guidelines"), list):
            return value
        entries = [
            {"source": entry} if isinstance(entry, str) else entry for entry in value["guidelines"]
        ]
        return {**value, "guidelines": entries}

    @field_validator("default_guidelines_path")
    @classmethod
    def validate_default_path(cls, value: str | None) -> str | None:
        """Validate the manifest-wide project-relative target."""
        return (
            _validate_relative_path(value, "default_guidelines_path") if value is not None else None
        )

    def bind_source_base(self, base_dir: Path | str) -> GuidelinesManifest:
        """Bind all local declarations to one project base.

        Args:
            base_dir:
                Directory containing the manifest.

        Returns:
            This manifest after binding each declaration.

        Raises:
            OSError: If the base directory cannot be resolved.
        """
        self._source_base_dir = Path(base_dir).expanduser().resolve()
        self._source_base_bound = True
        for declaration in self.guidelines:
            declaration.bind_source_base(self._source_base_dir)
        return self

    @property
    def normalized_default_guidelines_path(self) -> str | None:
        """Return the normalized default target, if present."""
        return (
            _normalize_target(self.default_guidelines_path)
            if self.default_guidelines_path
            else None
        )

    def find(
        self,
        source: str,
        *,
        base_dir: Path | str | None = None,
    ) -> GuidelineDeclaration | None:
        """Find a declaration by expression or canonical source identity.

        Args:
            source:
                Expression or canonical source to compare.
            base_dir:
                Optional project base for relative lookup.

        Returns:
            The first matching declaration, or ``None``.

        Raises:
            ValueError: If the query is unsafe or malformed.
        """
        comparison_base = (
            Path(base_dir).expanduser().resolve() if base_dir is not None else self._source_base_dir
        )
        canonical, _, _, _ = _source_details(source, comparison_base)
        declaration_base = self._source_base_dir if self._source_base_bound else comparison_base
        return next(
            (
                declaration
                for declaration in self.guidelines
                if declaration.canonical_source_for(declaration_base) == canonical
            ),
            None,
        )


class GuidelineFileRecord(BaseModel):
    """One managed source file and its last-owned content hash.

    .. versionchanged:: 0.2.0
        File records carry normalized project-relative targets and optional ownership hashes.

    Args:
        source_path:
            Safe path relative to the resolved source.
        target_path:
            Safe path relative to the consumer project.
        sha256:
            Optional lowercase SHA-256 hash for incomplete/adopted records.

    Raises:
        pydantic.ValidationError: If paths escape their roots or the hash is invalid.

    Examples:
        >>> GuidelineFileRecord(
        ...     source_path="guide.md", target_path=".agents/guide.md"
        ... ).sha256 is None
        True
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    source_path: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN.pattern)

    @field_validator("source_path", "target_path")
    @classmethod
    def validate_record_paths(
        cls,
        value: str,
        info: Any,
    ) -> str:
        """Reject absolute and traversal paths."""
        return _validate_relative_path(value, info.field_name)

    @field_validator("sha256")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        """Normalize managed hashes to lowercase."""
        if value is None:
            return None
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be exactly 64 hexadecimal characters")
        return value.lower()

    @property
    def normalized_target_path(self) -> str:
        """Return the stable target path representation."""
        return _normalize_target(self.target_path)


class GuidelinesLockEntry(BaseModel):
    """Resolved provenance and managed files for one declaration.

    .. versionchanged:: 0.2.0
        Entries retain selectors, aliases, targets, semantic-version provenance, and exact
        replay metadata.

    Args:
        expression:
            Original manifest source expression.
        name:
            Stable display name.
        source:
            Canonical source identity.
        source_type:
            ``local``, ``git``, ``github``, or ``gitlab``.
        requested_ref:
            Revision requested by the declaration.
        path:
            Legacy literal source-relative selector.
        paths:
            Plural source selectors.
        pattern:
            Legacy filename-stem glob.
        reference_kind:
            Provider-classified revision kind.
        resolved_ref:
            Revision actually captured.
        resolved_version:
            Resolved version where applicable.
        commit:
            Resolved Git commit.
        captured_at:
            UTC capture timestamp.
        semver_constraint:
            Original semantic-version constraint, when used.
        resolved_tag:
            Tag selected for a semantic-version constraint.
        resolution_timestamp:
            UTC time at which the semantic-version tag was selected.
        target_path:
            Project-relative destination.
        alias:
            Declaration alias, when configured.
        files:
            Managed source-to-target records.

    Raises:
        pydantic.ValidationError: If provenance, paths, timestamps, or hashes are invalid.

    Examples:
        >>> entry = GuidelinesLockEntry(
        ...     expression="./guidelines/",
        ...     name="guidelines",
        ...     source="./guidelines/",
        ...     source_type="local",
        ...     resolved_ref="working-tree",
        ...     target_path=".github/guidelines",
        ... )
        >>> entry.is_complete()
        True
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    expression: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_type: SourceType
    requested_ref: str | None = None
    path: str | None = None
    paths: list[str] | None = None
    reference_kind: ReferenceKind | None = None
    resolved_ref: str | None = None
    resolved_version: str | None = None
    commit: str | None = None
    captured_at: datetime = Field(default_factory=_utc_now)
    semver_constraint: str | None = None
    resolved_tag: str | None = None
    resolution_timestamp: datetime | None = None
    pattern: str | None = None
    target_path: str | None = None
    alias: str | None = None
    files: list[GuidelineFileRecord] = Field(default_factory=list)

    @field_validator("expression", "name")
    @classmethod
    def validate_identity_text(
        cls,
        value: str,
        info: Any,
    ) -> str:
        """Reject whitespace-only generated identity metadata."""
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        if info.field_name == "expression":
            _source_details(value)
        return value.strip()

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        """Normalize the durable source identity."""
        return _source_details(value)[0]

    @field_validator(
        "requested_ref",
        "resolved_ref",
        "resolved_version",
        "semver_constraint",
        "resolved_tag",
    )
    @classmethod
    def validate_revision_metadata(cls, value: str | None) -> str | None:
        """Reject unsafe lock revision values."""
        return _validate_revision(value) if value is not None else None

    @field_validator("path")
    @classmethod
    def validate_entry_path(cls, value: str | None) -> str | None:
        """Validate copied literal source path metadata."""
        return _validate_source_path(value)

    @field_validator("paths")
    @classmethod
    def validate_entry_paths(cls, value: list[str] | None) -> list[str] | None:
        """Validate copied plural selectors."""
        if value is None:
            return None
        if not value:
            raise ValueError("paths must contain at least one selector")
        return [_validate_selector(item) for item in value]

    @field_validator("pattern")
    @classmethod
    def validate_entry_pattern(cls, value: str | None) -> str | None:
        """Validate copied declaration pattern metadata."""
        return _validate_pattern_value(value)

    @field_validator("target_path")
    @classmethod
    def validate_entry_target(cls, value: str | None) -> str | None:
        """Validate copied project-relative target metadata."""
        return _validate_relative_path(value, "target_path") if value is not None else None

    @field_validator("alias")
    @classmethod
    def validate_entry_alias(cls, value: str | None) -> str | None:
        """Validate copied declaration alias metadata."""
        return _validate_alias(value)

    @field_validator("captured_at", "resolution_timestamp")
    @classmethod
    def normalize_timestamps(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        """Require timezone-aware timestamps and normalize to UTC."""
        return _as_utc(value, info.field_name) if value is not None else None

    @field_validator("commit")
    @classmethod
    def normalize_commit(cls, value: str | None) -> str | None:
        """Validate and lowercase a resolved Git commit."""
        if value is None:
            return None
        if not _COMMIT_PATTERN.fullmatch(value):
            raise ValueError("commit must be 7 to 64 hexadecimal characters")
        return value.lower()

    @model_validator(mode="after")
    def validate_provenance_groups(self) -> GuidelinesLockEntry:
        """Require complete semver and selector provenance groups."""
        if self.semver_constraint is not None and (
            self.resolved_tag is None or self.resolution_timestamp is None
        ):
            raise ValueError(
                "semver provenance requires semver_constraint, resolved_tag, and "
                "resolution_timestamp"
            )
        if self.semver_constraint is None and (
            self.resolved_tag is not None or self.resolution_timestamp is not None
        ):
            raise ValueError(
                "semver provenance requires semver_constraint when resolved_tag or "
                "resolution_timestamp is provided"
            )
        if self.paths is not None and (self.path is not None or self.pattern is not None):
            raise ValueError("paths cannot be combined with path or pattern")
        return self

    def is_complete(self) -> bool:
        """Return whether this entry can support frozen replay."""
        exact_revision = (
            bool(self.resolved_ref)
            if self.source_type == "local"
            else bool(self.commit and re.fullmatch(r"[0-9a-fA-F]{40}", self.commit))
        )
        return bool(
            self.expression
            and self.name
            and self.source
            and exact_revision
            and self.target_path
            and all(record.sha256 for record in self.files)
        )

    def matches(
        self,
        declaration: GuidelineDeclaration,
        *,
        base_dir: Path | None = None,
    ) -> bool:
        """Return whether source, revision, selectors, target, and alias match.

        Args:
            declaration:
                Manifest declaration to compare.
            base_dir:
                Optional project base for local identity.

        Returns:
            ``True`` only for a declaration-specific lock entry.
        """
        canonical = declaration.canonical_source_for(base_dir)
        _, source_type, source_ref, _ = _source_details(
            declaration.source,
            base_dir if base_dir is not None else declaration.source_base_dir,
        )
        requested_ref = declaration.ref or source_ref
        target_matches = declaration.normalized_target_path is None or (
            self.target_path is not None
            and _normalize_target(self.target_path) == declaration.normalized_target_path
        )
        return (
            self.source == canonical
            and self.source_type == source_type
            and self.requested_ref == requested_ref
            and self.path == declaration.path
            and self.paths == declaration.paths
            and self.pattern == declaration.pattern
            and target_matches
            and self.alias == declaration.alias
        )

    @property
    def normalized_target_path(self) -> str | None:
        """Return the stable target path, if configured."""
        return _normalize_target(self.target_path) if self.target_path else None


class GuidelinesLock(BaseModel):
    """Versioned neutral ``guidelines.lock.json`` document.

    .. versionchanged:: 0.2.0
        Lock entries carry deterministic declaration identity and frozen-replay provenance.

    Args:
        version:
            Document version, retained at ``1``.
        manager:
            Neutral manager identity, ``ai-guidelines``.
        lock_format:
            Neutral lock format identity, ``ai-guidelines``.
        lock_format_version:
            Major lock format version, currently ``1``.
        manager_version:
            Version of the manager that generated the document.
        generated_at:
            UTC document-generation timestamp.
        guidelines:
            Resolved entries in manifest order.

    Raises:
        pydantic.ValidationError: If compatibility metadata or entries are invalid.

    Examples:
        >>> GuidelinesLock().lock_format
        'ai-guidelines'
    """

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    version: Literal[1] = 1
    manager: Literal["ai-guidelines"] = "ai-guidelines"
    lock_format: Literal["ai-guidelines"] = "ai-guidelines"
    lock_format_version: Literal[1] = 1
    manager_version: str = Field(default="0.1.0", min_length=1)
    generated_at: datetime = Field(default_factory=_utc_now)
    guidelines: list[GuidelinesLockEntry] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def validate_compatibility_metadata(cls, value: Any) -> Any:
        """Reject foreign documents and unsupported lock-format majors.

        A lockfile produced by any other tool is rejected generically: this
        tool has no knowledge of, and no migration path from, foreign lockfile
        dialects. Unrecognized top-level keys are tolerated for forward
        compatibility only when the document explicitly identifies itself as an
        ai-guidelines lockfile; otherwise they mark it as foreign.
        """
        if not isinstance(value, dict):
            return value
        if set(value) - set(cls.model_fields) and "lock_format" not in value:
            raise ValueError("unrecognized lockfile format; regenerate it")
        if value.get("version") not in (None, 1):
            raise ValueError("unsupported lock document major version")
        lock_format = value.get("lock_format")
        if lock_format not in (None, "ai-guidelines"):
            raise ValueError("unsupported lock format; expected ai-guidelines")
        format_version = value.get("lock_format_version")
        if format_version not in (None, 1):
            raise ValueError("unsupported lock format major version")
        manager = value.get("manager")
        if manager not in (None, "ai-guidelines"):
            raise ValueError("unsupported lock manager; expected ai-guidelines")
        return value

    @field_validator("manager_version")
    @classmethod
    def validate_manager_version(cls, value: str) -> str:
        """Reject structurally blank generator versions."""
        if not value.strip():
            raise ValueError("manager_version must not be blank")
        return value.strip()

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        """Require an aware UTC document timestamp."""
        return _as_utc(value, "generated_at")

    def find_entry(
        self,
        declaration: GuidelineDeclaration,
        *,
        base_dir: Path | None = None,
    ) -> GuidelinesLockEntry | None:
        """Find the first declaration-specific matching entry."""
        return next(
            (entry for entry in self.guidelines if entry.matches(declaration, base_dir=base_dir)),
            None,
        )

    def is_complete_for(
        self,
        declaration: GuidelineDeclaration,
        *,
        base_dir: Path | None = None,
    ) -> bool:
        """Return whether a matching entry has replay provenance and hashes."""
        entry = self.find_entry(declaration, base_dir=base_dir)
        return bool(entry and entry.is_complete())

    def missing_or_incomplete(
        self,
        declaration: GuidelineDeclaration,
        *,
        base_dir: Path | None = None,
    ) -> GuidelinesLockEntry | None:
        """Return a matching incomplete entry, excluding an absent declaration."""
        entry = self.find_entry(declaration, base_dir=base_dir)
        return entry if entry is not None and not entry.is_complete() else None
