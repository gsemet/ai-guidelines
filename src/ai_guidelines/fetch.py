"""Acquire local and remote guideline sources through one safe interface."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import unquote, urlsplit

import platformdirs
from pydantic import BaseModel, ConfigDict, Field

from ai_guidelines.locations import (
    LocationParseError,
    SourceLocation,
    parse_location,
    validate_revision,
    validate_source_location,
    with_source_path,
)

GitRunner = Callable[[Sequence[str], Path | None], str]
ReferenceKind = Literal["branch", "tag", "ambiguous", "commit", "unknown", "local"]

_SEMVER_TAG = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?$"
)
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")


class SourceFetchError(RuntimeError):
    """Raised when a guideline source cannot be acquired safely."""


class _SourcePathNotFoundError(SourceFetchError):
    """Report a missing repository path with a safe correction hint."""

    def __init__(self, location: SourceLocation, suggested_path: str | None = None) -> None:
        message = (
            f"Guideline path '{location.relative_path}' was not found in the repository. "
            "Repository fragment paths are relative to the repository root."
        )
        if suggested_path is not None:
            reference = location.requested_ref or "HEAD"
            message += (
                f" Try the repository-relative path '{suggested_path}' "
                f"(for example, '#{reference}:{suggested_path}')."
            )
        super().__init__(message)


def _suffix_variants(path: str) -> tuple[str, ...]:
    """Return equivalent source paths for the supported guideline suffixes."""
    if path.endswith(".guideline.md"):
        stem = path[: -len(".guideline.md")]
    elif path.endswith(".guidelines.md"):
        stem = path[: -len(".guidelines.md")]
    else:
        stem = path
    return (f"{stem}.guideline.md", f"{stem}.guidelines.md")


def _resolve_existing_source_path(
    root: Path, location: SourceLocation, runner: GitRunner
) -> tuple[Path, SourceLocation]:
    """Resolve a source path while accepting either supported filename suffix."""
    requested = location.relative_path
    candidates = (requested,) if requested == "." else (requested, *_suffix_variants(requested))
    for candidate in candidates:
        path = _safe_source_path(root, candidate)
        if path.exists():
            resolved = with_source_path(location, candidate) if candidate != requested else location
            if path.is_dir() and resolved.kind != "folder":
                resolved = resolved.model_copy(update={"kind": "folder"})
            return path, resolved
    raise _SourcePathNotFoundError(location, _suggest_source_path(root, location, runner))


class AcquiredSource(BaseModel):
    """Materialized source returned from an acquisition context."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    location: SourceLocation
    root: Path
    path: Path
    resolved_ref: str | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,64}$")
    reference_kind: ReferenceKind | None = None
    temporary: bool = False


class SourceFetcher:
    """Injectable acquisition facade for production code and tests."""

    def __init__(self, runner: GitRunner | None = None, temp_root: Path | None = None) -> None:
        self.runner = runner or _run_git
        self.temp_root = temp_root

    def acquire(
        self,
        location: SourceLocation | str,
        *,
        sparse_pattern: str | None = None,
        sparse_patterns: Sequence[str] | None = None,
        checkout_root: Path | None = None,
        base_dir: Path | None = None,
    ) -> AbstractContextManager[AcquiredSource]:
        """Return an acquisition context manager for one source."""
        return acquire_source(
            location,
            runner=self.runner,
            temp_root=self.temp_root,
            sparse_pattern=sparse_pattern,
            sparse_patterns=sparse_patterns,
            checkout_root=checkout_root,
            base_dir=base_dir,
        )

    def acquire_many(
        self,
        locations: Sequence[SourceLocation],
        *,
        sparse_patterns: Sequence[str] | None = None,
        checkout_root: Path | None = None,
    ) -> AbstractContextManager[list[AcquiredSource]]:
        """Acquire same-repository locations in one sparse checkout."""
        return _acquire_many(
            locations,
            runner=self.runner,
            temp_root=self.temp_root,
            sparse_patterns=sparse_patterns,
            checkout_root=checkout_root,
        )


def _run_git(args: Sequence[str], cwd: Path | None = None) -> str:
    """Run one Git command without a shell and return standard output."""
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _safe_source_path(root: Path, relative_path: str) -> Path:
    """Resolve a source path while enforcing checkout containment."""
    candidate = root if relative_path == "." else root / relative_path
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        if resolved_candidate != resolved_root and not resolved_candidate.is_relative_to(
            resolved_root
        ):
            raise SourceFetchError("source path escapes the acquired checkout")
        return resolved_candidate
    except OSError:
        raise SourceFetchError("source path could not be resolved safely") from None


def _validate_source_location(location: SourceLocation) -> SourceLocation:
    """Revalidate a public location model before using it for acquisition."""
    try:
        return validate_source_location(location)
    except (TypeError, ValueError):
        raise SourceFetchError("source location is unsafe") from None


def _validate_sparse_pattern(value: str) -> str:
    """Validate one additional sparse-checkout pattern before invoking Git."""
    if not isinstance(value, str):
        raise SourceFetchError("sparse patterns must be printable relative paths")
    decoded = unquote(value)
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        raise SourceFetchError("sparse patterns must contain printable characters")
    normalized = decoded.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith(("/", "!", "#"))
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or PureWindowsPath(normalized).drive
        or "!" in normalized
    ):
        raise SourceFetchError("sparse pattern is unsafe")
    parts = PurePosixPath(normalized).parts
    if any(part in {".", ".."} or part.startswith("-") for part in parts):
        raise SourceFetchError("sparse pattern is unsafe")
    return normalized


def _validate_sparse_patterns(
    sparse_pattern: str | None,
    sparse_patterns: Sequence[str] | None,
) -> tuple[str | None, tuple[str, ...]]:
    """Validate all caller-supplied sparse patterns before source acquisition."""
    if isinstance(sparse_patterns, str):
        raise SourceFetchError("sparse patterns must be a sequence of paths")
    validated_pattern = (
        _validate_sparse_pattern(sparse_pattern) if sparse_pattern is not None else None
    )
    validated_patterns = tuple(
        _validate_sparse_pattern(pattern) for pattern in (sparse_patterns or ())
    )
    return validated_pattern, validated_patterns


def _checkout_path(location: SourceLocation, cache_root: Path | None = None) -> Path:
    """Return a unique cache-root checkout path for one remote ref."""
    if location.repository is None:
        raise ValueError("local sources do not have a remote checkout path")
    root = cache_root or Path(platformdirs.user_cache_dir("ai-guidelines")) / "guideline-sources"
    parsed = urlsplit(location.repository)
    repository_name = parsed.netloc + parsed.path if parsed.netloc else location.repository
    repository_name = repository_name.rstrip("/").removesuffix(".git")
    readable_identity = re.sub(r"[^A-Za-z0-9._-]+", "_", repository_name).strip("._-")
    requested_ref = re.sub(r"[^A-Za-z0-9._-]+", "_", location.requested_ref or "HEAD").strip("._-")
    pending_name = f"{readable_identity or 'repository'}_{requested_ref or 'HEAD'}_pending"
    return root / f"{pending_name}_{uuid.uuid4().hex}"


def _remove_checkout(path: Path) -> None:
    """Remove a failed checkout without following a symlink."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except (FileNotFoundError, OSError):
        pass


def _prepare_checkout(path: Path) -> None:
    """Prepare one direct cache-root checkout destination."""
    _remove_checkout(path)
    path.parent.mkdir(parents=True, exist_ok=True)


def _local_git_commit(path: Path, runner: GitRunner) -> str | None:
    """Return the local repository HEAD when available."""
    cwd = path if path.is_dir() else path.parent
    try:
        commit = runner(["rev-parse", "HEAD"], cwd).strip()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None
    return commit if _COMMIT.fullmatch(commit) else None


def _suggest_source_path(root: Path, location: SourceLocation, runner: GitRunner) -> str | None:
    """Find one repository path containing the requested suffix."""
    try:
        output = runner(["ls-tree", "-r", "--name-only", "HEAD"], root)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None
    suffix = f"/{location.relative_path}"
    matches: set[str] = set()
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate or any(
            ord(character) < 32 or ord(character) == 127 for character in candidate
        ):
            continue
        if candidate == location.relative_path or candidate.endswith(suffix):
            matches.add(candidate)
            continue
        candidate_stem = candidate.removesuffix(".guidelines.md").removesuffix(".guideline.md")
        requested_stem = location.relative_path.removesuffix(".guidelines.md").removesuffix(
            ".guideline.md"
        )
        if candidate_stem == requested_stem or candidate.startswith(
            f"{location.relative_path.rstrip('/')}/"
        ):
            matches.add(candidate)
    return next(iter(matches)) if len(matches) == 1 else None


def _is_semver_constraint(value: str | None) -> bool:
    """Return whether a revision is a supported range expression."""
    return (
        value is not None
        and bool(value)
        and (
            any(value.startswith(operator) for operator in (">", "<", "=", "~", "^"))
            or "*" in value
        )
    )


def _version_tuple(tag: str) -> tuple[int, int, int] | None:
    """Parse a simple semantic-version tag."""
    match = _SEMVER_TAG.fullmatch(tag.strip())
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor") or 0),
        int(match.group("patch") or 0),
    )


def _matches_constraint(
    version: tuple[int, int, int], target: tuple[int, int, int], operator: str
) -> bool:
    """Compare one version against one semver operator."""
    if operator == "^":
        if target[0] > 0:
            upper_bound = (target[0] + 1, 0, 0)
        elif target[1] > 0:
            upper_bound = (target[0], target[1] + 1, 0)
        else:
            upper_bound = (target[0], target[1], target[2] + 1)
        return target <= version < upper_bound
    comparisons = {
        ">=": version >= target,
        "<=": version <= target,
        ">": version > target,
        "<": version < target,
        "~": version >= target and version[:2] == target[:2],
        "=": version == target,
    }
    return comparisons.get(operator, False)


def _satisfies(version: tuple[int, int, int], constraint: str) -> bool:
    """Evaluate common comma-separated semver comparisons."""
    for expression in (part.strip() for part in constraint.split(",")):
        if not expression:
            continue
        operator = "="
        for candidate in (">=", "<=", ">", "<", "^", "~", "="):
            if expression.startswith(candidate):
                operator = candidate
                expression = expression[len(candidate) :].strip()
                break
        if expression.endswith(".*"):
            try:
                prefix = tuple(int(part) for part in expression[:-2].split("."))
            except ValueError:
                return False
            if version[: len(prefix)] != prefix:
                return False
            continue
        target = _version_tuple(expression)
        if target is None or not _matches_constraint(version, target, operator):
            return False
    return True


def _resolve_semver_tag(repository: str, constraint: str, runner: GitRunner) -> str:
    """Resolve the highest matching semantic-version tag from Git metadata."""
    output = runner(["ls-remote", "--tags", repository], None)
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for line in output.splitlines():
        reference = line.split("\t", 1)[-1].removeprefix("refs/tags/").removesuffix("^{}")
        version = _version_tuple(reference)
        if version is not None and _satisfies(version, constraint):
            candidates.append((version, reference))
    if not candidates:
        raise SourceFetchError("no matching semantic-version revision was found")
    return max(candidates)[1]


def _classify_remote_reference(repository: str, reference: str, runner: GitRunner) -> ReferenceKind:
    """Classify an exact ref using provider metadata rather than its spelling."""
    if _COMMIT.fullmatch(reference):
        return "commit"
    try:
        branch_output = runner(["ls-remote", "--heads", repository, reference], None)
        tag_output = runner(["ls-remote", "--tags", repository, reference], None)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return "unknown"
    branches = False
    tags = False
    for line in branch_output.splitlines():
        remote_ref = line.split("\t", 1)[-1].removesuffix("^{}")
        branches = branches or remote_ref == f"refs/heads/{reference}"
    for line in tag_output.splitlines():
        remote_ref = line.split("\t", 1)[-1].removesuffix("^{}")
        tags = tags or remote_ref == f"refs/tags/{reference}"
    if branches and tags:
        return "ambiguous"
    if branches:
        return "branch"
    if tags:
        return "tag"
    # Some Git transports do not expose ref classification through the
    # injected metadata command. Git itself still validates the ref during
    # clone, so use the safe moving-ref default rather than rejecting it.
    return "branch"


def _clone_remote(
    location: SourceLocation,
    checkout: Path,
    runner: GitRunner,
    sparse_pattern: str | None = None,
    sparse_patterns: Sequence[str] | None = None,
) -> tuple[str | None, str | None, ReferenceKind]:
    """Clone and sparsely materialize one remote location."""
    if location.repository is None:
        raise SourceFetchError("remote location has no repository")
    validated_sparse_pattern, validated_sparse_patterns = _validate_sparse_patterns(
        sparse_pattern, sparse_patterns
    )
    requested_ref = location.requested_ref
    resolved_ref = requested_ref
    if resolved_ref is not None:
        try:
            resolved_ref = validate_revision(resolved_ref, error_type=LocationParseError)
        except (LocationParseError, ValueError):
            raise SourceFetchError("requested source revision is unsafe") from None
    if _is_semver_constraint(resolved_ref):
        try:
            resolved_ref = _resolve_semver_tag(location.repository, resolved_ref or "", runner)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            raise SourceFetchError("could not resolve the requested source revision") from None

    if requested_ref is None:
        reference_kind: ReferenceKind = "branch"
    elif _is_semver_constraint(requested_ref):
        reference_kind = "tag"
    else:
        reference_kind = _classify_remote_reference(
            location.repository, resolved_ref or requested_ref, runner
        )
    clone_command = ["clone", "--no-checkout"]
    if urlsplit(location.repository).scheme.lower() != "file":
        clone_command.append("--filter=blob:none")
    clone_command.append("--no-tags")
    if resolved_ref and not _COMMIT.fullmatch(resolved_ref):
        clone_command.extend(["--branch", resolved_ref])
    clone_command.extend([location.repository, str(checkout)])
    try:
        # Real Git accepts an empty destination directory. Creating it first
        # also gives injected runners the same destination contract as Git.
        checkout.mkdir(parents=True, exist_ok=True)
        runner(clone_command, None)
        runner(["sparse-checkout", "init", "--no-cone"], checkout)
        patterns: list[str] = []
        if location.relative_path != ".":
            sparse_path = location.relative_path
            if location.kind == "folder":
                sparse_path = f"{sparse_path.rstrip('/')}/*"
            patterns.append(sparse_path)
        if validated_sparse_pattern is not None:
            patterns.append(validated_sparse_pattern)
        patterns.extend(validated_sparse_patterns)
        if not patterns:
            patterns.append("*")
        runner(["sparse-checkout", "set", "--no-cone", *dict.fromkeys(patterns)], checkout)
        runner(["checkout", "--detach", resolved_ref or "HEAD"], checkout)
        commit = runner(["rev-parse", "HEAD"], checkout).strip() or None
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        raise SourceFetchError("could not acquire the requested guideline source") from None
    return resolved_ref, commit, reference_kind


@contextmanager
def acquire_source(
    location: SourceLocation | str,
    *,
    runner: GitRunner | None = None,
    temp_root: Path | None = None,
    base_dir: Path | None = None,
    sparse_pattern: str | None = None,
    sparse_patterns: Sequence[str] | None = None,
    checkout_root: Path | None = None,
) -> Iterator[AcquiredSource]:
    """Materialize one local or remote source for a bounded context."""
    sparse_pattern, sparse_patterns = _validate_sparse_patterns(sparse_pattern, sparse_patterns)
    if isinstance(location, str):
        parsed = parse_location(location, base_dir=base_dir)
    elif isinstance(location, SourceLocation):
        parsed = _validate_source_location(location)
    else:
        raise SourceFetchError("source location is unsafe")
    active_runner = runner or _run_git
    if parsed.source_type == "local":
        if parsed.local_path is None or not parsed.local_path.exists():
            raise SourceFetchError("local guideline source does not exist")
        commit = _local_git_commit(parsed.local_path, active_runner)
        yield AcquiredSource(
            location=parsed,
            root=parsed.local_path,
            path=parsed.local_path,
            commit=commit,
            reference_kind="local",
            temporary=False,
        )
        return

    root = checkout_root or _checkout_path(parsed, temp_root)
    try:
        _prepare_checkout(root)
        resolved_ref, commit, reference_kind = _clone_remote(
            parsed,
            root,
            active_runner,
            sparse_pattern=sparse_pattern,
            sparse_patterns=sparse_patterns,
        )
        source_path, parsed = _resolve_existing_source_path(root, parsed, active_runner)
    except _SourcePathNotFoundError:
        _remove_checkout(root)
        raise
    except SourceFetchError as error:
        _remove_checkout(root)
        raise error from None
    except (OSError, RuntimeError, TypeError, ValueError):
        _remove_checkout(root)
        raise SourceFetchError("could not acquire the requested guideline source") from None
    yield AcquiredSource(
        location=parsed,
        root=root,
        path=source_path,
        resolved_ref=resolved_ref,
        commit=commit,
        reference_kind=reference_kind,
        temporary=False,
    )


fetch_source = acquire_source
fetch_location = acquire_source


def _sparse_pattern(location: SourceLocation) -> str:
    """Return one literal non-cone sparse pattern."""
    pattern = location.relative_path
    if location.kind == "folder":
        return f"{pattern.rstrip('/')}/*"
    return pattern


def _sparse_patterns_for_location(location: SourceLocation) -> tuple[str, ...]:
    """Return sparse patterns covering both supported guideline suffixes."""
    if location.kind == "folder" or not location.relative_path.endswith(
        (".guideline.md", ".guidelines.md")
    ):
        path = location.relative_path.rstrip("/")
        return (path, f"{path}/*")
    return _suffix_variants(location.relative_path)


@contextmanager
def _acquire_many(
    locations: Sequence[SourceLocation],
    *,
    runner: GitRunner | None = None,
    temp_root: Path | None = None,
    sparse_patterns: Sequence[str] | None = None,
    checkout_root: Path | None = None,
) -> Iterator[list[AcquiredSource]]:
    """Acquire same-repository locations with a union of sparse patterns."""
    validated_locations = tuple(_validate_source_location(location) for location in locations)
    if not validated_locations:
        yield []
        return
    if any(location.source_type == "local" for location in validated_locations):
        raise ValueError("shared acquisition supports remote locations only")
    first = validated_locations[0]
    if any(
        location.repository != first.repository or location.requested_ref != first.requested_ref
        for location in validated_locations[1:]
    ):
        raise ValueError("shared acquisition requires one repository and revision")
    if first.repository is None:
        raise ValueError("shared acquisition requires a repository")
    root_location = first.model_copy(
        update={"relative_path": ".", "kind": "folder", "canonical_source": first.repository}
    )
    patterns = [
        pattern
        for location in validated_locations
        if location.relative_path != "."
        for pattern in _sparse_patterns_for_location(location)
    ]
    patterns.extend(sparse_patterns or ())
    with ExitStack() as stack:
        acquired_root = stack.enter_context(
            acquire_source(
                root_location,
                runner=runner,
                temp_root=temp_root,
                sparse_patterns=patterns,
                checkout_root=checkout_root,
            )
        )
        acquired: list[AcquiredSource] = []
        for location in validated_locations:
            source_path, resolved_location = _resolve_existing_source_path(
                acquired_root.root, location, runner or _run_git
            )
            acquired.append(
                AcquiredSource(
                    location=resolved_location,
                    root=acquired_root.root,
                    path=source_path,
                    resolved_ref=acquired_root.resolved_ref,
                    commit=acquired_root.commit,
                    reference_kind=acquired_root.reference_kind,
                    temporary=acquired_root.temporary,
                )
            )
        yield acquired
