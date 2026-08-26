"""Discover supported guideline files from acquired source material."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.frontmatter import read_guideline_frontmatter

logger = logging.getLogger(__name__)
GUIDELINE_SUFFIXES = (".guideline.md", ".guidelines.md")


class DiscoveryError(RuntimeError):
    """Raised when acquired guideline material cannot be inspected safely."""


class DiscoveredGuideline(BaseModel):
    """One source-relative guideline candidate and optional metadata."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    path: Path
    source_path: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    suffix_stripped_name: str = Field(min_length=1)

    @property
    def source_relative_path(self) -> str:
        """Return this candidate's source-relative path."""
        return self.source_path

    @property
    def filename(self) -> str:
        """Return this candidate's basename."""
        return Path(self.source_path).name


class DiscoveryResult(BaseModel):
    """Validated discovery output and non-fatal selection warnings."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    files: list[DiscoveredGuideline] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def candidates(self) -> list[DiscoveredGuideline]:
        """Return discovered files using the acquisition vocabulary."""
        return self.files


def is_guideline_file(path: Path | str) -> bool:
    """Return whether a basename ends in an exact supported suffix."""
    return Path(path).name.endswith(GUIDELINE_SUFFIXES)


def _suffix_stripped_name(path: Path) -> str:
    """Return a guideline basename without its supported suffix."""
    for suffix in GUIDELINE_SUFFIXES:
        if path.name.endswith(suffix):
            return path.name[: -len(suffix)]
    return path.name


def _metadata(path: Path) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Read complete metadata and flatten the common display fields."""
    frontmatter = read_guideline_frontmatter(path)
    nested = frontmatter.get("metadata")
    metadata = dict(nested) if isinstance(nested, dict) else {}
    name = str(frontmatter.get("name") or _suffix_stripped_name(path))
    description = str(frontmatter.get("description") or "")
    return frontmatter, metadata, name, description


def _candidate(path: Path, source_path: str) -> DiscoveredGuideline:
    """Build one candidate and turn malformed optional metadata into a warning."""
    try:
        complete, metadata, name, description = _metadata(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        logger.warning("Could not read guideline frontmatter for %s", path.name)
        complete, metadata, name, description = {}, {}, _suffix_stripped_name(path), ""
    return DiscoveredGuideline(
        path=path,
        source_path=source_path.replace("\\", "/"),
        name=name,
        description=description,
        metadata=metadata,
        frontmatter=complete,
        suffix_stripped_name=_suffix_stripped_name(path),
    )


def _resolve_contained_candidate(root: Path, candidate: Path) -> None:
    """Reject candidate symlinks that resolve outside the acquired root."""
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            raise DiscoveryError("discovered guideline path escapes the source root")
    except OSError:
        raise DiscoveryError("discovered guideline path could not be resolved safely") from None


def _validate_pattern(pattern: str | None) -> None:
    """Validate a case-sensitive filename-stem glob."""
    if pattern is not None and (not pattern or "/" in pattern or "\\" in pattern):
        raise ValueError("pattern must be a non-empty filename glob")


def _validate_paths(paths: Sequence[str] | None) -> None:
    """Validate selector paths before filesystem traversal."""
    if paths is None:
        return
    if not paths:
        raise ValueError("paths must contain at least one selector")
    for raw_path in paths:
        normalized = raw_path.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if (
            not normalized
            or normalized.startswith("/")
            or "\x00" in normalized
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            raise ValueError("paths entries must be safe relative source paths")


def _suffixless(relative_path: str) -> str:
    """Remove one exact supported suffix from a relative source path."""
    for suffix in GUIDELINE_SUFFIXES:
        if relative_path.endswith(suffix):
            return relative_path[: -len(suffix)]
    return relative_path


def _pattern_matches(path: Path, pattern: str) -> bool:
    """Match a pattern against the filename and its suffixless stem."""
    patterns = [pattern]
    if ".guideline?.md" in pattern:
        patterns.extend(
            pattern.replace(".guideline?.md", suffix)
            for suffix in (".guideline.md", ".guidelines.md")
        )
    return any(
        fnmatchcase(value, candidate_pattern)
        for value in (path.name, _suffix_stripped_name(path))
        for candidate_pattern in patterns
    )


def _selector_matches(relative_path: str, selector: str) -> bool:
    """Match full, suffixless, basename, and selected-directory forms."""
    normalized = selector.replace("\\", "/").rstrip("/")
    suffixless = _suffixless(relative_path)
    stem = Path(suffixless).name
    return any(
        fnmatchcase(candidate, normalized) or candidate.startswith(f"{normalized}/")
        for candidate in (relative_path, suffixless, stem)
    )


def _source_path_for_selector(relative_path: str, selector: str | None) -> str:
    """Flatten a directory selector while retaining nested namespace."""
    if selector is None:
        return relative_path
    normalized = selector.replace("\\", "/").rstrip("/")
    suffixless = _suffixless(relative_path)
    exact = {relative_path, suffixless, Path(suffixless).name}
    if normalized in exact or any(character in normalized for character in "*?["):
        return (
            relative_path
            if any(character in normalized for character in "*?[")
            else Path(relative_path).name
        )
    return relative_path.removeprefix(f"{normalized}/") or Path(relative_path).name


def _source_candidates(source: AcquiredSource) -> tuple[list[Path], list[str]]:
    """Return safe guideline candidates and their source-relative paths."""
    root = source.path
    if source.location.is_file and not source.path.is_dir():
        _resolve_contained_candidate(root, root)
        if root.is_file() and is_guideline_file(root):
            return [root], [root.name]
        return [], []
    if not root.is_dir():
        raise DiscoveryError("acquired guideline source is neither a file nor a folder")
    candidates: list[Path] = []
    relative_paths: list[str] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not candidate.is_file() or not is_guideline_file(candidate):
            continue
        _resolve_contained_candidate(root, candidate)
        candidates.append(candidate)
        relative_paths.append(candidate.relative_to(root).as_posix())
    return candidates, relative_paths


def _warning(source: AcquiredSource, pattern: str | None, paths: Sequence[str] | None) -> str:
    """Build a warning that omits sensitive source internals."""
    if paths is not None:
        selection = f" matching paths {list(paths)!r}"
    elif pattern is not None:
        selection = f" matching pattern {pattern!r}"
    else:
        selection = ""
    return f"No guideline files matched source {source.location.display_name!r}{selection}."


def discover_guidelines(
    source: AcquiredSource,
    *,
    pattern: str | None = None,
    paths: Sequence[str] | None = None,
) -> DiscoveryResult:
    """Recursively discover exact guideline suffixes from one acquired source."""
    _validate_pattern(pattern)
    _validate_paths(paths)
    if not source.path.exists():
        raise DiscoveryError("acquired guideline source path does not exist")
    candidates, relative_paths = _source_candidates(source)
    discovered: list[DiscoveredGuideline] = []
    for candidate, relative_path in zip(candidates, relative_paths, strict=True):
        if pattern is not None and not _pattern_matches(candidate, pattern):
            continue
        matching_selector = next(
            (selector for selector in paths or () if _selector_matches(relative_path, selector)),
            None,
        )
        if paths is not None and matching_selector is None:
            continue
        discovered.append(
            _candidate(candidate, _source_path_for_selector(relative_path, matching_selector))
        )
    # Directory selectors flatten their selected root. Keep direct files
    # before nested namespace entries, then sort deterministically within each
    # depth so the output is stable and easy to read.
    discovered.sort(key=lambda item: (item.source_path.count("/"), item.source_path))
    warnings: list[str] = []
    if not discovered:
        message = _warning(source, pattern, paths)
        warnings.append(message)
        logger.warning(message)
    return DiscoveryResult(files=discovered, warnings=warnings)


GuidelineCandidate = DiscoveredGuideline
GuidelineDiscovery = DiscoveryResult
discover_source_files = discover_guidelines
