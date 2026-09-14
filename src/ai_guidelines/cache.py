"""Disposable discovery and materialized-source caches outside project state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast
from urllib.parse import urlsplit

import platformdirs
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import ai_guidelines.atomic as atomic
from ai_guidelines.discovery import (
    GUIDELINE_SUFFIXES,
    DiscoveredGuideline,
    DiscoveryResult,
    is_guideline_file,
)
from ai_guidelines.locations import (
    LocationParseError,
    SourceLocation,
    parse_location,
    validate_source_location,
    with_source_path,
)

CACHE_TTL = timedelta(minutes=10)
GUIDELINE_CACHE_REFRESH_TTL = timedelta(minutes=10)
GUIDELINE_CACHE_EVICTION_TTL = timedelta(days=10)
_CACHE_VERSION = 1
_MATERIALIZED_CACHE_VERSION = 2


def _utc(value: datetime) -> datetime:
    """Normalize an injected timestamp to aware UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_cached_relative_path(value: str) -> str:
    """Validate a cached source-relative path before joining it to a root."""
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (
        not normalized
        or normalized.startswith("/")
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(normalized).drive
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("cached source path must be a safe relative path")
    return "/".join(parts)


class _CachedGuideline(BaseModel):
    """Serializable discovery metadata without checkout paths."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    frontmatter: dict[str, object] = Field(default_factory=dict)
    suffix_stripped_name: str = Field(min_length=1)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        """Keep persisted candidate paths relative and contained."""
        return _safe_cached_relative_path(value)


class _DiscoveryPayload(BaseModel):
    """Validated discovery-cache JSON envelope."""

    model_config = ConfigDict(extra="forbid")

    version: int = _CACHE_VERSION
    created_at: datetime
    source: str = Field(min_length=1)
    revision: str = ""
    relative_path: str = Field(min_length=1)
    files: list[_CachedGuideline] = Field(default_factory=list)


class DiscoveryCache:
    """Store short-lived source-relative discovery metadata.

    .. versionadded:: 0.2.0
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        ttl: timedelta = CACHE_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure a discovery cache with an injectable clock.

        Args:
            cache_dir:
                Optional directory for discovery metadata.
            ttl:
                Maximum age of a discovery entry.
            clock:
                Optional UTC timestamp provider used by tests.

        Raises:
            ValueError:
                If ``ttl`` is not positive.

        Examples:
            >>> cache = DiscoveryCache(ttl=timedelta(minutes=5))
            >>> cache.ttl == timedelta(minutes=5)
            True
        """
        self.cache_dir = (
            Path(cache_dir).expanduser()
            if cache_dir is not None
            else Path(platformdirs.user_cache_dir("ai-guidelines")) / "guidelines"
        )
        if ttl <= timedelta(0):
            raise ValueError("cache TTL must be positive")
        self.ttl = ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        """Return current UTC time."""
        return _utc(self._clock())

    @staticmethod
    def _identity(location: SourceLocation | str, revision: str | None = None) -> dict[str, str]:
        """Build credential-free source/revision/path identity."""
        if isinstance(location, str):
            try:
                parsed = parse_location(location)
            except (LocationParseError, OSError, TypeError, ValueError):
                raise ValueError(
                    "cache source must be a supported location without credentials"
                ) from None
        elif isinstance(location, SourceLocation):
            try:
                parsed = validate_source_location(location)
            except (TypeError, ValueError):
                raise ValueError("cache source location is unsafe or inconsistent") from None
        else:
            raise TypeError("cache location must be a SourceLocation or string")
        return {
            "source": parsed.canonical_source,
            "revision": revision if revision is not None else parsed.requested_ref or "",
            "relative_path": parsed.relative_path,
        }

    def cache_key(
        self,
        location: SourceLocation | str,
        revision: str | None = None,
    ) -> str:
        """Return a deterministic SHA-256 cache identity.

        Args:
            location:
                Local or remote source identity.
            revision:
                Optional revision override used in the cache identity.

        Returns:
            Lowercase SHA-256 identity text.
        """
        encoded = json.dumps(
            self._identity(location, revision), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    key_for = cache_key

    def cache_path(
        self,
        location: SourceLocation | str,
        revision: str | None = None,
    ) -> Path:
        """Return the cache file path without creating it.

        Args:
            location:
                Source identity used to derive the cache key.
            revision:
                Optional revision override.

        Returns:
            The JSON metadata path for the source.
        """
        return self.cache_dir / f"{self.cache_key(location, revision)}.json"

    @staticmethod
    def _source_path(root: Path | None, source_path: str) -> Path:
        """Reconstruct one candidate path under an optional acquired root."""
        relative = _safe_cached_relative_path(source_path)
        if root is None:
            return Path(*PurePosixPath(relative).parts)
        if root.is_file():
            if relative != root.name:
                raise ValueError("cached file source path does not match its source")
            candidate, boundary = root, root.parent
        else:
            candidate, boundary = root / Path(*PurePosixPath(relative).parts), root
        if not candidate.resolve(strict=False).is_relative_to(boundary.resolve(strict=False)):
            raise ValueError("cached source path escapes its source root")
        return candidate

    def read(
        self,
        location: SourceLocation | str,
        *,
        revision: str | None = None,
        root: Path | None = None,
        refresh: bool = False,
        bypass: bool = False,
    ) -> DiscoveryResult | None:
        """Read a fresh entry, or return a miss for absent or corrupt state.

        Args:
            location:
                Source identity used to find the entry.
            revision:
                Optional revision override used in the cache identity.
            root:
                Optional acquired root used to reconstruct candidate paths.
            refresh:
                Remove the entry and force a miss.
            bypass:
                Return a miss without reading or deleting cache state.

        Returns:
            Discovery metadata, or ``None`` when the entry is unavailable.

        Examples:
            >>> cache = DiscoveryCache()
            >>> cache.read("github/example/repo") is None
            True
        """
        path = self.cache_path(location, revision)
        if bypass:
            return None
        if refresh:
            self._unlink(path)
            return None
        try:
            payload = _DiscoveryPayload.model_validate_json(path.read_text(encoding="utf-8"))
            identity = self._identity(location, revision)
            if (
                payload.version != _CACHE_VERSION
                or payload.source != identity["source"]
                or payload.revision != identity["revision"]
                or payload.relative_path != identity["relative_path"]
                or self._now() - _utc(payload.created_at) >= self.ttl
            ):
                raise ValueError("discovery cache entry is stale")
            return DiscoveryResult(
                files=[
                    DiscoveredGuideline(
                        path=self._source_path(root, item.source_path),
                        source_path=item.source_path,
                        name=item.name,
                        description=item.description,
                        metadata=item.metadata,
                        frontmatter=item.frontmatter,
                        suffix_stripped_name=item.suffix_stripped_name,
                    )
                    for item in payload.files
                ]
            )
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ):
            self._unlink(path)
            return None

    def write(
        self,
        location: SourceLocation | str,
        result: DiscoveryResult,
        *,
        revision: str | None = None,
    ) -> None:
        """Atomically persist source-relative discovery metadata.

        Args:
            location:
                Source identity represented by ``result``.
            result:
                Discovery metadata to serialize.
            revision:
                Optional revision override used in the cache identity.

        Examples:
            >>> from tempfile import TemporaryDirectory
            >>> with TemporaryDirectory() as directory:
            ...     cache = DiscoveryCache(cache_dir=Path(directory))
            ...     cache.write("github/example/repo", DiscoveryResult())
            ...     cache.read("github/example/repo") is not None
            True
        """
        identity = self._identity(location, revision)
        payload = _DiscoveryPayload(
            created_at=self._now(),
            source=identity["source"],
            revision=identity["revision"],
            relative_path=identity["relative_path"],
            files=[
                _CachedGuideline(
                    source_path=item.source_path,
                    name=item.name,
                    description=item.description,
                    metadata=item.metadata,
                    frontmatter=item.frontmatter,
                    suffix_stripped_name=item.suffix_stripped_name,
                )
                for item in result.files
            ],
        )
        path = self.cache_path(location, revision)
        temporary_name: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload.model_dump_json())
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                self._unlink(Path(temporary_name))

    @staticmethod
    def _unlink(path: Path) -> None:
        """Remove a file while tolerating cleanup races."""
        with suppress(FileNotFoundError, OSError):
            path.unlink()

    def clear(
        self,
        location: SourceLocation | str,
        revision: str | None = None,
    ) -> None:
        """Remove one discovery entry."""
        self._unlink(self.cache_path(location, revision))


class GuidelineCacheSnapshot(BaseModel):
    """One materialized repository snapshot returned by the cache.

    .. versionadded:: 0.2.0
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    root: Path
    repository: str
    requested_ref: str | None
    resolved_ref: str | None
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    reference_kind: str | None
    paths: tuple[str, ...]
    resolved_paths: tuple[tuple[str, str], ...] = ()

    def relative_path_for(self, requested_path: str) -> str:
        """Return the materialized path for one requested source path."""
        normalized = "." if requested_path == "." else _safe_cached_relative_path(requested_path)
        return dict(self.resolved_paths).get(normalized, normalized)

    def path_for(self, requested_path: str) -> Path:
        """Return a contained materialized path for one requested source path."""
        relative_path = self.relative_path_for(requested_path)
        candidate = (
            self.root
            if relative_path == "."
            else self.root.joinpath(*PurePosixPath(relative_path).parts)
        )
        try:
            if not candidate.resolve(strict=False).is_relative_to(self.root.resolve(strict=False)):
                raise ValueError("cached source path escapes its source root")
        except OSError:
            raise ValueError("cached source path could not be resolved safely") from None
        return candidate

    def location_for(self, location: SourceLocation) -> SourceLocation:
        """Return a fresh source location using the resolved cached path."""
        path = self.path_for(location.relative_path)
        return with_source_path(
            location,
            self.relative_path_for(location.relative_path),
            kind="folder" if path.is_dir() else "file",
        )


class MaterializedGuidelineCache:
    """Cache sparse repository snapshots by credential-free identity and commit.

    .. versionchanged:: 0.2.0
        Pending checkouts are promoted atomically and failed publication restores
        the previous snapshot.

    A pending checkout is promoted into its commit-addressed path only after
    its source tree has been validated. Metadata publication is atomic, and
    failed publication restores the previous snapshot.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        refresh_ttl: timedelta = GUIDELINE_CACHE_REFRESH_TTL,
        eviction_ttl: timedelta = GUIDELINE_CACHE_EVICTION_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure refresh, eviction, and time behavior for the cache.

        Args:
            cache_dir:
                Optional directory for materialized snapshots and metadata.
            refresh_ttl:
                Maximum age for reusing a moving-reference snapshot.
            eviction_ttl:
                Maximum idle age before a snapshot is pruned.
            clock:
                Optional UTC timestamp provider used by tests.

        Raises:
            ValueError:
                If either TTL is non-positive or eviction precedes refresh.

        Examples:
            >>> cache = MaterializedGuidelineCache(eviction_ttl=timedelta(days=2))
            >>> cache.eviction_ttl == timedelta(days=2)
            True
        """
        self.cache_dir = (
            Path(cache_dir).expanduser()
            if cache_dir is not None
            else Path(platformdirs.user_cache_dir("ai-guidelines")) / "guideline-sources"
        )
        if refresh_ttl <= timedelta(0) or eviction_ttl <= timedelta(0):
            raise ValueError("guideline cache TTLs must be positive")
        if eviction_ttl < refresh_ttl:
            raise ValueError("guideline cache eviction TTL must not precede refresh TTL")
        self.refresh_ttl = refresh_ttl
        self.eviction_ttl = eviction_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        """Return current UTC time."""
        return _utc(self._clock())

    @staticmethod
    def repository_identity(location: SourceLocation) -> str:
        """Return a credential-free host/repository identity.

        Args:
            location:
                Validated remote source location.

        Returns:
            Stable host and repository text without credentials.

        Raises:
            ValueError:
                If the location is local or inconsistent.
        """
        try:
            location = validate_source_location(location)
        except (TypeError, ValueError):
            raise ValueError("cache source location is unsafe or inconsistent") from None
        if location.repository is None:
            raise ValueError("local sources cannot be stored in the remote guideline cache")
        repository = location.repository
        if repository.startswith("git@"):
            host, separator, path = repository[4:].partition(":")
            if not separator:
                raise ValueError("remote repository identity is invalid")
            return f"{host.lower()}/{path.rstrip('/').removesuffix('.git')}"
        parsed = urlsplit(repository)
        if parsed.hostname:
            host = parsed.hostname.lower()
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            return f"{host}{parsed.path.rstrip('/').removesuffix('.git')}"
        return repository.rstrip("/").removesuffix(".git")

    @staticmethod
    def _normalize_commit(commit: str) -> str:
        """Validate one full hexadecimal commit identity."""
        normalized = commit.strip().lower()
        if len(normalized) != 40 or not re.fullmatch(r"[0-9a-f]{40}", normalized):
            raise ValueError("guideline cache identity requires a full commit")
        return normalized

    def cache_key(
        self,
        location: SourceLocation,
        commit: str,
    ) -> str:
        """Return a readable, collision-resistant repository/commit key.

        Args:
            location:
                Remote source location.
            commit:
                Full 40-character hexadecimal Git commit.

        Returns:
            Credential-free cache directory name.

        Raises:
            ValueError:
                If the location or commit is unsafe.
        """
        identity = self.repository_identity(location)
        repository = re.sub(r"[^A-Za-z0-9._-]+", "_", identity).strip("._-")
        identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{repository or 'repository'}__{identity_digest}__{self._normalize_commit(commit)}"

    def cache_path(
        self,
        location: SourceLocation,
        commit: str,
    ) -> Path:
        """Return the materialized entry path.

        Args:
            location:
                Remote source location.
            commit:
                Full 40-character hexadecimal Git commit.

        Returns:
            Commit-addressed snapshot directory, which is not created.
        """
        return self.cache_dir / self.cache_key(location, commit)

    def checkout_path(self, location: SourceLocation) -> Path:
        """Return a unique pending checkout path directly under the cache root."""
        if location.repository is None:
            raise ValueError("local sources cannot have a remote checkout path")
        repository = re.sub(r"[^A-Za-z0-9._-]+", "_", self.repository_identity(location)).strip(
            "._-"
        )
        reference = re.sub(r"[^A-Za-z0-9._-]+", "_", location.requested_ref or "HEAD").strip("._-")
        return (
            self.cache_dir
            / f"{repository or 'repository'}_{reference or 'HEAD'}_pending_{uuid.uuid4().hex}"
        )

    @property
    def metadata_path(self) -> Path:
        """Return the atomically replaced metadata file."""
        return self.cache_dir / "cache.yaml"

    @property
    def lock_path(self) -> Path:
        """Return the metadata path compatibility alias."""
        return self.metadata_path

    @property
    def metadata_lock_path(self) -> Path:
        """Return the stable advisory sidecar lock path."""
        return self.cache_dir / "cache.lock.yaml"

    @contextmanager
    def _metadata_lock(self) -> Iterator[None]:
        """Serialize each read-modify-write metadata transaction."""
        with atomic.advisory_lock(self.metadata_lock_path):
            yield

    def _empty_payload(self) -> dict[str, object]:
        """Return a fresh metadata envelope."""
        return {
            "version": 1,
            "refresh_ttl_seconds": int(self.refresh_ttl.total_seconds()),
            "eviction_ttl_seconds": int(self.eviction_ttl.total_seconds()),
            "entries": [],
        }

    def _load_metadata(self) -> dict[str, object]:
        """Load metadata, treating corruption as an empty cache."""
        try:
            payload = yaml.safe_load(self.metadata_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return self._empty_payload()
            return payload
        except (FileNotFoundError, OSError, TypeError, yaml.YAMLError):
            return self._empty_payload()

    def _save_metadata(self, payload: dict[str, object]) -> None:
        """Atomically publish metadata while holding the sidecar lock."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload["version"] = 1
        payload["refresh_ttl_seconds"] = int(self.refresh_ttl.total_seconds())
        payload["eviction_ttl_seconds"] = int(self.eviction_ttl.total_seconds())
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.cache_dir,
                prefix=".cache.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                yaml.safe_dump(payload, temporary, sort_keys=False)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, self.metadata_path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                self._remove(Path(temporary_name))

    @staticmethod
    def _entries(payload: dict[str, object]) -> list[dict[str, object]]:
        """Return dictionary metadata entries only."""
        entries = payload.get("entries", [])
        return (
            [entry for entry in entries if isinstance(entry, dict)]
            if isinstance(entries, list)
            else []
        )

    def _entry_path(self, key: str) -> Path | None:
        """Return a safe direct child for one persisted entry key."""
        if not key or Path(key).name != key or key in {".", ".."}:
            return None
        candidate = self.cache_dir / key
        try:
            return (
                candidate
                if candidate.resolve(strict=False).parent == self.cache_dir.resolve(strict=False)
                else None
            )
        except OSError:
            return None

    @staticmethod
    def _validate_snapshot_tree(root: Path) -> None:
        """Reject snapshots containing symlinks that escape their source root."""
        if root.is_symlink() or not root.is_dir():
            raise ValueError("source snapshot must be a real directory")
        try:
            resolved_root = root.resolve(strict=False)
            directories = [root]
            while directories:
                directory = directories.pop()
                for candidate in directory.iterdir():
                    if candidate.is_symlink():
                        if not candidate.resolve(strict=False).is_relative_to(resolved_root):
                            raise ValueError("source symlink escapes the source root")
                    elif candidate.is_dir():
                        directories.append(candidate)
        except OSError:
            raise ValueError("source snapshot could not be inspected safely") from None

    def _cleanup_payload(
        self,
        payload: dict[str, object],
        *,
        snapshot_backups: list[tuple[Path, Path]] | None = None,
    ) -> bool:
        """Remove metadata and snapshots older than the eviction TTL."""
        entries = self._entries(payload)
        retained: list[dict[str, object]] = []
        changed = False
        now = self._now()
        for entry in entries:
            try:
                accessed = _utc(
                    datetime.fromisoformat(str(entry["last_accessed_at"]).replace("Z", "+00:00"))
                )
                expired = now - accessed >= self.eviction_ttl
            except (KeyError, TypeError, ValueError):
                expired = True
            if expired:
                key = entry.get("key")
                if isinstance(key, str):
                    path = self._entry_path(key)
                    if path is not None:
                        if snapshot_backups is None or not path.exists():
                            self._remove(path)
                        else:
                            backup = self.cache_dir / (
                                f".{path.name}.eviction-backup-{uuid.uuid4().hex}"
                            )
                            os.replace(path, backup)
                            snapshot_backups.append((path, backup))
                changed = True
            else:
                retained.append(entry)
        if changed or not isinstance(payload.get("entries"), list):
            payload["entries"] = retained
            return True
        return False

    def cleanup(self) -> int:
        """Delete snapshots not accessed within the eviction TTL."""
        with self._metadata_lock():
            payload = self._load_metadata()
            before = len(self._entries(payload))
            changed = self._cleanup_payload(payload)
            if changed:
                self._save_metadata(payload)
            return before - len(self._entries(payload))

    def size(self) -> int:
        """Return the number of bytes occupied by cache entries and metadata."""
        if not self.cache_dir.exists():
            return 0
        total = 0
        for path in self.cache_dir.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def clear_all(self) -> int:
        """Remove cached snapshots while preserving the advisory lock sidecar."""
        with self._metadata_lock():
            removed = len(self._entries(self._load_metadata()))
            if self.cache_dir.exists():
                for path in self.cache_dir.iterdir():
                    if path.name != self.metadata_lock_path.name:
                        self._remove(path)
            return removed

    def _snapshot_for_entry(
        self,
        location: SourceLocation,
        entry: dict[str, object],
    ) -> GuidelineCacheSnapshot | None:
        """Validate one metadata entry and resolve its requested path."""
        key, repository, commit = entry.get("key"), entry.get("repository"), entry.get("commit")
        if (
            not isinstance(key, str)
            or not isinstance(repository, str)
            or not isinstance(commit, str)
        ):
            return None
        path = self._entry_path(key)
        if path is None or not path.is_dir():
            return None
        try:
            normalized_commit = self._normalize_commit(commit)
            requested = location.relative_path
            resolved_paths: dict[str, str] = {}
            raw_resolved_paths = entry.get("resolved_paths", {})
            if isinstance(raw_resolved_paths, dict):
                for raw_requested, raw_resolved in raw_resolved_paths.items():
                    if not isinstance(raw_requested, str) or not isinstance(raw_resolved, str):
                        continue
                    requested_path = (
                        "." if raw_requested == "." else _safe_cached_relative_path(raw_requested)
                    )
                    resolved_path = (
                        "." if raw_resolved == "." else _safe_cached_relative_path(raw_resolved)
                    )
                    resolved_paths[requested_path] = resolved_path
            resolved = resolved_paths.get(requested, requested)
            candidate = path if resolved == "." else path.joinpath(*PurePosixPath(resolved).parts)
            if not candidate.exists() or not candidate.resolve().is_relative_to(path.resolve()):
                return None
            raw_paths = entry.get("paths", [])
            paths = tuple(str(item) for item in raw_paths) if isinstance(raw_paths, list) else ()
            requested_ref = entry.get("requested_ref")
            resolved_ref = entry.get("resolved_ref")
            reference_kind = entry.get("reference_kind")
            return GuidelineCacheSnapshot(
                root=path,
                repository=repository,
                requested_ref=requested_ref
                if isinstance(requested_ref, str) and requested_ref
                else None,
                resolved_ref=resolved_ref
                if isinstance(resolved_ref, str) and resolved_ref
                else None,
                commit=normalized_commit,
                reference_kind=reference_kind
                if isinstance(reference_kind, str) and reference_kind
                else None,
                paths=paths,
                resolved_paths=tuple(sorted(resolved_paths.items())),
            )
        except (FileNotFoundError, OSError, TypeError, ValueError, ValidationError):
            return None

    @staticmethod
    def _snapshot_guideline_paths(root: Path) -> list[str]:
        """Return safe, supported guideline paths materialized below *root*."""
        try:
            resolved_root = root.resolve()
        except OSError:
            return []
        paths: list[str] = []
        for candidate in root.rglob("*"):
            try:
                if (
                    not candidate.is_file()
                    or not is_guideline_file(candidate)
                    or not candidate.resolve().is_relative_to(resolved_root)
                ):
                    continue
                paths.append(candidate.relative_to(root).as_posix())
            except (OSError, ValueError):
                continue
        return paths

    @staticmethod
    def _selector_matches_snapshot_path(relative_path: str, selector: str) -> bool:
        """Return whether a manifest selector identifies a materialized path."""
        normalized_selector = selector.replace("\\", "/").rstrip("/")
        suffixless = relative_path
        for suffix in GUIDELINE_SUFFIXES:
            if suffixless.endswith(suffix):
                suffixless = suffixless[: -len(suffix)]
                break
        stem = Path(suffixless).name
        return any(
            fnmatchcase(candidate, normalized_selector)
            or candidate.startswith(f"{normalized_selector}/")
            for candidate in (relative_path, suffixless, stem)
        ) or any(relative_path == f"{normalized_selector}{suffix}" for suffix in GUIDELINE_SUFFIXES)

    @staticmethod
    def _pattern_matches_snapshot_path(relative_path: str, pattern: str) -> bool:
        """Return whether a legacy filename pattern identifies a materialized path."""
        basename = Path(relative_path).name
        for suffix in GUIDELINE_SUFFIXES:
            if basename.endswith(suffix):
                basename = basename[: -len(suffix)]
                break
        return fnmatchcase(basename, pattern)

    def _snapshot_covers(
        self,
        root: Path,
        location: SourceLocation,
        *,
        required_paths: Sequence[str] | None,
        required_pattern: str | None,
    ) -> bool:
        """Return whether a snapshot contains every requested selector.

        Examples:
            >>> cache = MaterializedGuidelineCache(cache_dir="/tmp/ai-guidelines-cache")
            >>> location = parse_location(
            ...     "https://example.com/team/repo#main:guidelines/"
            ... )
            >>> cache._snapshot_covers(
            ...     Path("/tmp/ai-guidelines-cache/snapshot"),
            ...     location,
            ...     required_paths=None,
            ...     required_pattern=None,
            ... )
            True
        """
        if required_paths is None and required_pattern is None:
            return True
        selection_root = root
        if required_paths is None and location.relative_path != ".":
            selection_root = root.joinpath(*PurePosixPath(location.relative_path).parts)
            if not selection_root.is_dir():
                selection_root = root
        materialized_paths = self._snapshot_guideline_paths(selection_root)
        if required_paths is not None and not all(
            any(self._selector_matches_snapshot_path(path, selector) for path in materialized_paths)
            for selector in required_paths
        ):
            return False
        return required_pattern is None or any(
            self._pattern_matches_snapshot_path(path, required_pattern)
            for path in materialized_paths
        )

    def lookup(
        self,
        location: SourceLocation,
        *,
        touch: bool = True,
        required_paths: Sequence[str] | None = None,
        required_pattern: str | None = None,
    ) -> GuidelineCacheSnapshot | None:
        """Return a fresh snapshot matching repository and requested revision.

        ``touch=False`` is used by frozen replay so validating cached state does
        not refresh cache metadata or otherwise mutate the filesystem. A lookup
        with selectors also verifies that the sparse snapshot contains the
        requested guideline coverage before reusing it.

        Args:
            location:
                Remote source location to resolve.
            touch:
                Update access metadata when a snapshot is reused.
            required_paths:
                Optional plural selectors that the snapshot must contain.
            required_pattern:
                Optional legacy filename pattern that the snapshot must contain.

        Returns:
            A validated snapshot, or ``None`` when no reusable state exists.

        Examples:
            >>> cache = MaterializedGuidelineCache()
            >>> cache.lookup(parse_location("https://example.com/team/repo")) is None
            True
        """
        if location.repository is None:
            return None

        def read_snapshot() -> GuidelineCacheSnapshot | None:
            """Read one cache snapshot, optionally updating access metadata."""
            payload = self._load_metadata()
            changed = self._cleanup_payload(payload) if touch else False
            repository = self.repository_identity(location)
            requested = location.requested_ref or ""
            now = self._now()
            for entry in self._entries(payload):
                if entry.get("repository") != repository:
                    continue
                commit_match = bool(
                    len(requested) == 40 and entry.get("commit") == requested.lower()
                )
                if entry.get("requested_ref", "") != requested and not commit_match:
                    continue
                try:
                    refreshed = _utc(
                        datetime.fromisoformat(
                            str(entry["last_refreshed_at"]).replace("Z", "+00:00")
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if not commit_match and now - refreshed >= self.refresh_ttl:
                    continue
                snapshot = self._snapshot_for_entry(location, entry)
                if snapshot is None:
                    continue
                if not self._snapshot_covers(
                    snapshot.root,
                    location,
                    required_paths=required_paths,
                    required_pattern=required_pattern,
                ):
                    continue
                if touch:
                    entry["last_accessed_at"] = now.isoformat().replace("+00:00", "Z")
                    changed = True
                if changed:
                    self._save_metadata(payload)
                return snapshot
            if changed:
                self._save_metadata(payload)
            return None

        if touch:
            with self._metadata_lock():
                return read_snapshot()
        return read_snapshot()

    def write_snapshot(
        self,
        location: SourceLocation,
        checkout_root: Path,
        locations: Sequence[SourceLocation],
        *,
        resolved_locations: Sequence[SourceLocation] | None = None,
        resolved_ref: str | None,
        commit: str,
        reference_kind: str | None,
    ) -> GuidelineCacheSnapshot:
        """Atomically publish one union sparse checkout and metadata entry.

        Args:
            location:
                Repository identity used for the cache key.
            checkout_root:
                Validated pending checkout to promote or copy.
            locations:
                Requested source locations represented by the checkout.
            resolved_locations:
                Optional locations after suffix or path resolution.
            resolved_ref:
                Revision selected for acquisition.
            commit:
                Full commit resolved by Git.
            reference_kind:
                Provider classification for the requested reference.

        Returns:
            The published snapshot rooted at its final cache path.

        Raises:
            OSError:
                If filesystem or metadata publication fails.
            ValueError:
                If the checkout, locations, or commit is unsafe.

        Examples:
            >>> from tempfile import TemporaryDirectory
            >>> with TemporaryDirectory() as directory:
            ...     root = Path(directory)
            ...     checkout = root / "checkout"
            ...     source = checkout / "guide.guidelines.md"
            ...     source.parent.mkdir(parents=True)
            ...     _ = source.write_text("guide\\n", encoding="utf-8")
            ...     location = parse_location(
            ...         "https://example.com/team/repo#main:guide.guidelines.md"
            ...     )
            ...     snapshot = MaterializedGuidelineCache(
            ...         cache_dir=root / "cache"
            ...     ).write_snapshot(
            ...         location,
            ...         checkout,
            ...         [location],
            ...         resolved_ref="main",
            ...         commit="a" * 40,
            ...         reference_kind="branch",
            ...     )
            ...     snapshot.path_for(location.relative_path).read_text(encoding="utf-8").strip()
            'guide'
        """
        try:
            location = validate_source_location(location)
            validated_locations = tuple(validate_source_location(item) for item in locations)
            validated_resolved_locations = (
                tuple(validate_source_location(item) for item in resolved_locations)
                if resolved_locations is not None
                else validated_locations
            )
        except (TypeError, ValueError):
            raise ValueError("cache source location is unsafe or inconsistent") from None
        if len(validated_locations) != len(validated_resolved_locations):
            raise ValueError("cache source and resolved location counts must match")
        if location.repository is None or not checkout_root.is_dir():
            raise ValueError("guideline snapshots require a remote checkout directory")
        self._validate_snapshot_tree(checkout_root)
        normalized_commit = self._normalize_commit(commit)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self.cache_key(location, normalized_commit)
        entry_path = self.cache_path(location, normalized_commit)
        cache_owned = checkout_root.parent.resolve() == self.cache_dir.resolve()
        now = self._now()
        paths = {source.relative_path for source in validated_locations}
        backup_path: Path | None = None
        staging_path: Path | None = None
        eviction_backups: list[tuple[Path, Path]] = []
        published = False
        with self._metadata_lock():
            try:
                payload = self._load_metadata()
                metadata_before = (
                    self.metadata_path.read_bytes() if self.metadata_path.exists() else None
                )
                existing = next(
                    (item for item in self._entries(payload) if item.get("key") == key), None
                )
                if existing is not None and isinstance(existing.get("paths"), list):
                    paths.update(str(item) for item in cast(list[object], existing["paths"]))
                resolved_paths: dict[str, str] = {}
                if existing is not None and isinstance(existing.get("resolved_paths"), dict):
                    stored_resolved_paths = cast(dict[object, object], existing["resolved_paths"])
                    for raw_requested, raw_resolved in stored_resolved_paths.items():
                        if isinstance(raw_requested, str) and isinstance(raw_resolved, str):
                            try:
                                requested_path = (
                                    "."
                                    if raw_requested == "."
                                    else _safe_cached_relative_path(raw_requested)
                                )
                                resolved_path = (
                                    "."
                                    if raw_resolved == "."
                                    else _safe_cached_relative_path(raw_resolved)
                                )
                            except ValueError:
                                continue
                            resolved_paths[requested_path] = resolved_path
                for requested_location, resolved_location in zip(
                    validated_locations, validated_resolved_locations, strict=True
                ):
                    resolved_paths[requested_location.relative_path] = (
                        resolved_location.relative_path
                    )
                if cache_owned:
                    if entry_path.exists():
                        self._validate_snapshot_tree(entry_path)
                        shutil.copytree(
                            entry_path,
                            checkout_root,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git"),
                            symlinks=True,
                        )
                        backup_path = self.cache_dir / (
                            f".{entry_path.name}.backup-{uuid.uuid4().hex}"
                        )
                        os.replace(entry_path, backup_path)
                    os.replace(checkout_root, entry_path)
                    published = True
                else:
                    staging_path = self.cache_dir / (
                        f".{entry_path.name}.staging-{uuid.uuid4().hex}"
                    )
                    self._remove(staging_path)
                    if entry_path.exists():
                        self._validate_snapshot_tree(entry_path)
                        staging_path.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(entry_path, staging_path, dirs_exist_ok=True, symlinks=True)
                    shutil.copytree(
                        checkout_root,
                        staging_path,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git"),
                        symlinks=True,
                    )
                    self._validate_snapshot_tree(staging_path)
                    if entry_path.exists():
                        backup_path = self.cache_dir / (
                            f".{entry_path.name}.backup-{uuid.uuid4().hex}"
                        )
                        os.replace(entry_path, backup_path)
                    os.replace(staging_path, entry_path)
                    staging_path = None
                    published = True
                entry: dict[str, object] = {
                    "key": key,
                    "repository": self.repository_identity(location),
                    "requested_ref": location.requested_ref or "",
                    "resolved_ref": resolved_ref or "",
                    "reference_kind": reference_kind or "",
                    "commit": normalized_commit,
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "last_accessed_at": now.isoformat().replace("+00:00", "Z"),
                    "last_refreshed_at": now.isoformat().replace("+00:00", "Z"),
                    "paths": sorted(paths),
                    "resolved_paths": dict(sorted(resolved_paths.items())),
                }
                payload["entries"] = [
                    item for item in self._entries(payload) if item.get("key") != key
                ] + [entry]
                self._cleanup_payload(payload, snapshot_backups=eviction_backups)
                self._save_metadata(payload)
                snapshot = self._snapshot_for_entry(location, entry)
                if snapshot is None:
                    raise ValueError("stored guideline snapshot could not be validated")
                if backup_path is not None:
                    self._remove(backup_path)
                    backup_path = None
                for _, eviction_backup in eviction_backups:
                    self._remove(eviction_backup)
                return snapshot
            except Exception as error:
                if published:
                    self._remove(entry_path)
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, entry_path)
                    backup_path = None
                if staging_path is not None:
                    self._remove(staging_path)
                if cache_owned:
                    self._remove(checkout_root)
                for original, eviction_backup in reversed(eviction_backups):
                    if eviction_backup.exists():
                        os.replace(eviction_backup, original)
                try:
                    if metadata_before is None:
                        self._remove(self.metadata_path)
                    else:
                        atomic.atomic_write(self.metadata_path, metadata_before)
                except OSError as rollback_error:
                    raise OSError("cache publication rollback failed") from rollback_error
                raise error

    @staticmethod
    def _remove(path: Path) -> None:
        """Remove one file or directory while tolerating cleanup races."""
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except (FileNotFoundError, OSError):
            pass


MaterializedSourceCache = MaterializedGuidelineCache
GuidelineSourceCache = MaterializedGuidelineCache
