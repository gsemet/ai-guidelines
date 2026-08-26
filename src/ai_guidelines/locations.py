"""Parse and validate provider-neutral guideline source locations."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_guidelines._validation import (
    PATH_METACHARACTERS as _PATH_METACHARACTERS,
)
from ai_guidelines._validation import (
    REMOTE_SCHEMES as _REMOTE_SCHEMES,
)
from ai_guidelines._validation import (
    has_control_characters,
    validate_fragment_expression,
    validate_remote_expression,
    validate_remote_field,
)
from ai_guidelines._validation import (
    source_type_for_host as _source_type_for_host,
)
from ai_guidelines._validation import (
    validate_revision as _validate_revision_primitive,
)

LocationKind = Literal["file", "folder"]
SourceType = Literal["gitlab", "github", "git", "local"]

_GITHUB_SHORT_FORM = re.compile(r"^[^./~][^/\\]*/[^/\\]+(?:/.*)?$")


class LocationParseError(ValueError):
    """Raised when a source expression is unsupported or unsafe."""


def validate_revision(value: str, *, error_type: type[ValueError] = LocationParseError) -> str:
    """Validate one revision before it becomes a Git argument.

    Args:
        value: Candidate branch, tag, or commit expression.
        error_type: Exception type raised on rejection.

    Returns:
        The decoded, stripped revision.

    Raises:
        ValueError: Of ``error_type``, when the revision is unsafe.
    """
    return _validate_revision_primitive(value, error_type=error_type)


class SourceLocation(BaseModel):
    """Validated identity for a local or remote guideline source."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    expression: str = Field(min_length=1)
    source_type: SourceType
    repository: str | None = None
    relative_path: str = Field(min_length=1)
    requested_ref: str | None = None
    kind: LocationKind
    canonical_source: str = Field(min_length=1)
    local_path: Path | None = None

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, value: str) -> str:
        """Reject blank, control-bearing, or credential-bearing expressions."""
        if has_control_characters(value):
            raise ValueError("location expression must contain printable characters")
        value = value.strip()
        if not value:
            raise ValueError("location expression must not be empty")
        _validate_remote_field(value)
        return value

    @field_validator("requested_ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        """Normalize an optional revision without accepting unsafe values."""
        return validate_revision(value) if value is not None else None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """Reject absolute, traversing, wildcard, or option-like source paths."""
        return _normalize_relative_path(value)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str | None) -> str | None:
        """Reject credentials retained in a remote repository field."""
        if value is not None:
            _validate_remote_field(value)
        return value

    @field_validator("canonical_source")
    @classmethod
    def validate_canonical_source(cls, value: str) -> str:
        """Reject credentials retained in a canonical identity."""
        _validate_remote_field(value)
        return value

    @property
    def ref(self) -> str | None:
        """Return the requested revision."""
        return self.requested_ref

    @property
    def path(self) -> str:
        """Return the source-relative path."""
        return self.relative_path

    @property
    def is_file(self) -> bool:
        """Return whether this location identifies one file."""
        return self.kind == "file"

    @property
    def is_folder(self) -> bool:
        """Return whether this location identifies a folder."""
        return self.kind == "folder"

    @property
    def display_name(self) -> str:
        """Return a suffix-stripped display name."""
        relative_path = str(self.model_dump()["relative_path"])
        if relative_path != ".":
            value = relative_path.rsplit("/", 1)[-1]
        elif self.repository:
            value = self.repository.rstrip("/").rsplit("/", 1)[-1]
        elif self.local_path:
            value = self.local_path.name
        else:
            value = "guidelines"
        for suffix in (".guidelines.md", ".guideline.md"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                break
        return value.removesuffix(".git") or "guidelines"

    @property
    def canonical(self) -> str:
        """Return the canonical source identity."""
        return self.canonical_source


def validate_source_location(location: SourceLocation) -> SourceLocation:
    """Revalidate and cross-check a public source location model.

    Pydantic's ``model_copy(update=...)`` intentionally bypasses field
    validators. Acquisition and cache boundaries therefore reconstruct the
    model and verify that its canonical identity still describes the supplied
    repository or local path.
    """
    try:
        validated = SourceLocation.model_validate(location.model_dump())
        if validated.source_type == "local":
            if validated.repository is not None or validated.local_path is None:
                raise LocationParseError("source location fields are inconsistent")
            local_path = validated.local_path.expanduser().resolve(strict=False)
            if validated.canonical_source != local_path.as_posix():
                raise LocationParseError("source location fields are inconsistent")
            expected_relative_path = local_path.name if validated.kind == "file" else "."
            if validated.relative_path != expected_relative_path:
                raise LocationParseError("source location fields are inconsistent")
        else:
            if validated.repository is None or validated.local_path is not None:
                raise LocationParseError("source location fields are inconsistent")
            repository = _remote_repository(validated.repository, validated.source_type)
            expected_canonical = (
                repository
                if validated.relative_path == "."
                else f"{repository}/{validated.relative_path}"
            )
            if validated.canonical_source != expected_canonical:
                raise LocationParseError("source location fields are inconsistent")
            if validated.relative_path == "." and validated.kind != "folder":
                raise LocationParseError("source location fields are inconsistent")
        return validated
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocationParseError("source location is unsafe or inconsistent") from None


def _validate_fragment_expression(fragment: str) -> None:
    """Reject credential-like key/value data embedded in a fragment."""
    validate_fragment_expression(fragment, error_type=LocationParseError, subject="location")


def _validate_remote_expression(expression: str) -> None:
    """Validate a remote expression without exposing sensitive input."""
    validate_remote_expression(expression, error_type=LocationParseError, subject="location")


def _validate_remote_field(value: str) -> None:
    """Validate credentials for a possibly remote model field."""
    validate_remote_field(value, error_type=LocationParseError, subject="location")


def _normalize_relative_path(value: str) -> str:
    """Normalize a source-relative path and reject unsafe sparse scopes."""
    decoded = unquote(value).replace("\\", "/")
    if "\x00" in decoded or decoded.startswith("/"):
        raise LocationParseError("location path must be relative")
    if has_control_characters(decoded):
        raise LocationParseError("location path must contain printable literal characters")
    if any(character in _PATH_METACHARACTERS for character in decoded):
        raise LocationParseError("location path must be a literal path")
    parts: list[str] = []
    for part in decoded.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise LocationParseError("location path must not contain traversal")
        if part.startswith(("#", "-")):
            raise LocationParseError("location path components must be literal and safe")
        parts.append(part)
    normalized = "/".join(parts) or "."
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(normalized).is_absolute():
        raise LocationParseError("location path must be relative")
    if PureWindowsPath(normalized).drive:
        raise LocationParseError("location path must be relative")
    return normalized


def _remote_repository(repository: str, source_type: SourceType) -> str:
    """Normalize a repository identity without query or fragment data."""
    repository = repository.rstrip("/")
    if source_type in {"github", "gitlab"}:
        repository = repository.removesuffix(".git")
    if repository.startswith(("http://", "https://")):
        parsed = urlsplit(repository)
        _validate_remote_expression(repository)
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    _validate_remote_expression(repository)
    return repository


def _split_fragment(fragment: str) -> tuple[str | None, str | None]:
    """Split optional ``ref:path`` fragment syntax."""
    if not fragment:
        return None, None
    decoded = unquote(fragment)
    _validate_fragment_expression(decoded)
    if ":" in decoded:
        requested_ref, path = decoded.split(":", 1)
        if not requested_ref.strip():
            raise LocationParseError("location revision must not be empty")
        return validate_revision(requested_ref), path
    return validate_revision(decoded), None


def _make_remote_location(
    expression: str,
    *,
    source_type: SourceType,
    repository: str,
    relative_path: str,
    requested_ref: str | None,
    kind: LocationKind,
) -> SourceLocation:
    """Construct one validated remote location."""
    relative_path = _normalize_relative_path(relative_path)
    repository = _remote_repository(repository, source_type)
    canonical = repository if relative_path == "." else f"{repository}/{relative_path}"
    return SourceLocation(
        expression=expression,
        source_type=source_type,
        repository=repository,
        relative_path=relative_path,
        requested_ref=requested_ref,
        kind=kind,
        canonical_source=canonical,
    )


def _parse_gitlab_url(
    expression: str, parsed: SplitResult, scheme: str, path: str
) -> SourceLocation:
    """Parse a GitLab ``/-/blob`` or ``/-/tree`` URL."""
    repository_path, remainder = path.split("/-/", 1)
    endpoint, separator, endpoint_path = remainder.partition("/")
    if endpoint not in {"blob", "tree"} or not separator:
        raise LocationParseError("unsupported GitLab location")
    raw_ref, separator, raw_source_path = endpoint_path.partition("/")
    if not raw_ref or not separator:
        raise LocationParseError("GitLab location must include a revision and path")
    source_path = unquote(raw_source_path)
    return _make_remote_location(
        expression,
        source_type="gitlab",
        repository=f"{scheme}://{parsed.netloc}/{unquote(repository_path.strip('/'))}",
        relative_path=source_path,
        requested_ref=unquote(raw_ref),
        kind="folder" if endpoint == "tree" or path.endswith("/") else "file",
    )


def _parse_github_tree_url(
    expression: str,
    parsed: SplitResult,
    scheme: str,
    path_parts: list[str],
    path: str,
) -> SourceLocation:
    """Parse a GitHub ``/tree`` or ``/blob`` URL."""
    owner, repository, endpoint = path_parts[:3]
    if len(path_parts) < 5:
        raise LocationParseError("GitHub location must include a revision and path")
    requested_ref = unquote(path_parts[3])
    source_path = "/".join(unquote(part) for part in path_parts[4:])
    return _make_remote_location(
        expression,
        source_type="github",
        repository=f"{scheme}://{parsed.netloc}/{unquote(owner)}/{unquote(repository)}",
        relative_path=source_path,
        requested_ref=requested_ref,
        kind="folder" if endpoint == "tree" or path.endswith("/") else "file",
    )


def _parse_provider_url(expression: str, parsed: SplitResult) -> SourceLocation:
    """Parse a provider-friendly or direct Git URL."""
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if not host:
        raise LocationParseError("location must identify a repository")
    _validate_remote_expression(expression)
    source_type = _source_type_for_host(host)
    raw_path = parsed.path.replace("\\", "/")
    path_parts = [part for part in raw_path.strip("/").split("/") if part]

    if source_type == "gitlab" and "/-/" in raw_path:
        return _parse_gitlab_url(expression, parsed, scheme, raw_path)
    if source_type == "github" and not path_parts:
        raise LocationParseError("GitHub location must identify a repository")
    if source_type == "github" and len(path_parts) >= 3 and path_parts[2] in {"blob", "tree"}:
        return _parse_github_tree_url(expression, parsed, scheme, path_parts, raw_path)

    fragment_ref, fragment_path = _split_fragment(parsed.fragment)
    compact_repository_path, separator, compact_source_path = raw_path.rpartition(":")
    if separator and compact_repository_path.strip("/") and compact_source_path.strip("/"):
        if fragment_path is not None:
            raise LocationParseError(
                "location cannot combine repository:path and #ref:path source syntax"
            )
        source_path = unquote(compact_source_path)
        return _make_remote_location(
            expression,
            source_type=source_type,
            repository=urlunsplit(
                (scheme, parsed.netloc, unquote(compact_repository_path.rstrip("/")), "", "")
            ),
            relative_path=source_path,
            requested_ref=fragment_ref,
            kind="file" if source_path.endswith((".guideline.md", ".guidelines.md")) else "folder",
        )

    if not path_parts:
        raise LocationParseError("location must identify a repository")
    source_path = fragment_path or "."
    return _make_remote_location(
        expression,
        source_type=source_type,
        repository=urlunsplit((scheme, parsed.netloc, raw_path.rstrip("/"), "", "")),
        relative_path=source_path,
        requested_ref=fragment_ref,
        kind="folder" if fragment_path is None or source_path.endswith("/") else "file",
    )


def _parse_scp_url(expression: str) -> SourceLocation:
    """Parse a Git scp-style URL and optional fragment."""
    base, separator, fragment = expression.partition("#")
    if "/" not in base or ":" not in base:
        raise LocationParseError("unsupported Git location")
    user_host, path_separator, repository_path = base.partition(":")
    if not path_separator or not user_host or not repository_path:
        raise LocationParseError("unsupported Git location")
    repository_path = _normalize_relative_path(repository_path)
    _validate_remote_expression(f"ssh://{user_host}/{repository_path}")
    requested_ref, fragment_path = _split_fragment(fragment) if separator else (None, None)
    source_path = fragment_path or "."
    return _make_remote_location(
        expression,
        source_type="git",
        repository=f"{user_host}:{repository_path}",
        relative_path=source_path,
        requested_ref=requested_ref,
        kind="folder" if fragment_path is None or source_path.endswith("/") else "file",
    )


def _parse_short_github(expression: str) -> SourceLocation:
    """Parse ``owner/repository[/path]`` and ``github/repository[/path]``."""
    base, separator, fragment = expression.partition("#")
    requested_ref, fragment_path = _split_fragment(fragment) if separator else (None, None)
    parts = [part for part in base.replace("\\", "/").split("/") if part]
    if len(parts) < 2:
        raise LocationParseError("location must identify a GitHub repository")
    if parts[0] == "github":
        owner = None
        repository = parts[1]
        short_path = "/".join(parts[2:])
    else:
        owner, repository = parts[:2]
        short_path = "/".join(parts[2:])
    source_path = fragment_path if fragment_path is not None else short_path or "."
    return _make_remote_location(
        expression,
        source_type="github",
        repository=(
            f"https://github.com/{repository}"
            if owner is None
            else f"https://github.com/{owner}/{repository}"
        ),
        relative_path=source_path,
        requested_ref=requested_ref,
        kind="folder" if fragment_path is None and not source_path.endswith(".md") else "file",
    )


def resolve_source_base(base_dir: Path | str | None = None) -> Path:
    """Resolve the base directory for relative local source expressions."""
    return Path(base_dir if base_dir is not None else Path.cwd()).expanduser().resolve()


def resolve_local_source_path(expression: str, base_dir: Path | None = None) -> Path:
    """Resolve a local source while enforcing base and symlink containment."""
    root = resolve_source_base(base_dir)
    raw_path = Path(expression).expanduser()
    resolved = (raw_path if raw_path.is_absolute() else root / raw_path).resolve(strict=False)
    if not raw_path.is_absolute() and not resolved.is_relative_to(root):
        raise LocationParseError("local location must remain within its base directory")
    return resolved


def _looks_local(expression: str, base_dir: Path | None) -> bool:
    """Return whether local path parsing has precedence."""
    path = Path(expression).expanduser()
    return bool(
        path.is_absolute()
        or expression.startswith((".", "~"))
        or PureWindowsPath(expression).drive
        or (base_dir is not None and (base_dir / path).exists())
    )


def _parse_local(expression: str, base_dir: Path | None) -> SourceLocation:
    """Resolve one local file or folder location."""
    raw_path = Path(expression).expanduser()
    if not raw_path.is_absolute() and ".." in raw_path.parts:
        raise LocationParseError("local location must not contain traversal")
    if not raw_path.is_absolute():
        root = resolve_source_base(base_dir)
        current = root
        for component in raw_path.parts:
            current /= component
            if current.is_symlink():
                raise LocationParseError("local location must not escape through a symlink")
    local_path = resolve_local_source_path(expression, base_dir)
    if local_path.exists():
        kind: LocationKind = "file" if local_path.is_file() else "folder"
    else:
        kind = (
            "folder"
            if expression.endswith(("/", "\\"))
            else "file"
            if local_path.suffix
            else "folder"
        )
    return SourceLocation(
        expression=expression,
        source_type="local",
        relative_path=local_path.name if kind == "file" else ".",
        kind=kind,
        canonical_source=local_path.as_posix(),
        local_path=local_path,
    )


def parse_location(
    expression: str,
    *,
    ref: str | None = None,
    base_dir: Path | None = None,
) -> SourceLocation:
    """Parse a supported local, Git, GitHub, or GitLab source expression."""
    if not isinstance(expression, str) or not expression:
        raise LocationParseError("location must be a non-empty string")
    if has_control_characters(expression):
        raise LocationParseError("location must contain printable characters")
    expression = expression.strip()
    if not expression:
        raise LocationParseError("location must be a non-empty string")
    try:
        if _looks_local(expression, base_dir):
            location = _parse_local(expression, base_dir)
        elif expression.startswith("git@"):
            location = _parse_scp_url(expression)
        elif _GITHUB_SHORT_FORM.fullmatch(expression):
            location = _parse_short_github(expression)
        else:
            parsed = urlsplit(expression)
            if parsed.scheme.lower() in _REMOTE_SCHEMES and parsed.netloc:
                location = _parse_provider_url(expression, parsed)
            else:
                raise LocationParseError("location must identify a supported repository or path")
        if ref is not None:
            location = location.model_copy(update={"requested_ref": validate_revision(ref)})
        return location
    except LocationParseError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise LocationParseError("location could not be parsed safely") from exc


def with_source_path(
    location: SourceLocation,
    relative_path: str,
    *,
    kind: LocationKind | None = None,
) -> SourceLocation:
    """Return a remote location scoped to one safe repository path."""
    normalized = _normalize_relative_path(relative_path)
    if location.source_type == "local":
        if location.local_path is None:
            raise LocationParseError("local source path is unavailable")
        local_path = (location.local_path / normalized).resolve(strict=False)
        return location.model_copy(
            update={
                "relative_path": normalized,
                "kind": "file",
                "canonical_source": local_path.as_posix(),
                "local_path": local_path,
            }
        )
    if location.repository is None:
        raise LocationParseError("source path overrides require a remote repository")
    selected_kind = kind or ("folder" if relative_path.endswith(("/", "\\")) else "file")
    canonical = location.repository if normalized == "." else f"{location.repository}/{normalized}"
    try:
        return SourceLocation.model_validate(
            {
                **location.model_dump(),
                "relative_path": normalized,
                "kind": selected_kind,
                "canonical_source": canonical,
            }
        )
    except (TypeError, ValueError) as error:
        raise LocationParseError("source path could not be validated safely") from error


GuidelineLocation = SourceLocation
ParsedLocation = SourceLocation
parse_source_location = parse_location
parse_source = parse_location


def canonicalize_source(expression: str, *, base_dir: Path | None = None) -> str:
    """Return the canonical identity for a source expression."""
    return parse_location(expression, base_dir=base_dir).canonical_source
