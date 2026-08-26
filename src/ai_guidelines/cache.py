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
    """Store short-lived source-relative discovery metadata."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        ttl: timedelta = CACHE_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
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

    def cache_key(self, location: SourceLocation | str, revision: str | None = None) -> str:
        """Return a deterministic SHA-256 cache identity."""
        encoded = json.dumps(
            self._identity(location, revision), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    key_for = cache_key

    def cache_path(self, location: SourceLocation | str, revision: str | None = None) -> Path:
        """Return the cache file path without creating it."""
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
        """Read a fresh entry, or return a miss for absent/corrupt state."""
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
        """Atomically persist source-relative discovery metadata."""
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

    def clear(self, location: SourceLocation | str, revision: str | None = None) -> None:
        """Remove one discovery entry."""
        self._unlink(self.cache_path(location, revision))


class GuidelineCacheSnapshot(BaseModel):
    """One materialized repository snapshot returned by the cache."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    root: Path
    repository: str
    requested_ref: str | None
    resolved_ref: str | None
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    reference_kind: str | None
    paths: tuple[str, ...]


class MaterializedGuidelineCache:
    """Cache sparse repository snapshots by credential-free identity and commit."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        refresh_ttl: timedelta = GUIDELINE_CACHE_REFRESH_TTL,
        eviction_ttl: timedelta = GUIDELINE_CACHE_EVICTION_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
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
        """Return a credential-free host/repository identity."""
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

    def cache_key(self, location: SourceLocation, commit: str) -> str:
        """Return a readable, collision-resistant repository/commit key."""
        identity = self.repository_identity(location)
        repository = re.sub(r"[^A-Za-z0-9._-]+", "_", identity).strip("._-")
        identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{repository or 'repository'}__{identity_digest}__{self._normalize_commit(commit)}"

    def cache_path(self, location: SourceLocation, commit: str) -> Path:
        """Return the materialized entry path."""
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

    def _cleanup_payload(self, payload: dict[str, object]) -> bool:
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
                        self._remove(path)
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
        self, location: SourceLocation, entry: dict[str, object]
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
            candidate = path if requested == "." else path.joinpath(*PurePosixPath(requested).parts)
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
        """Return whether a snapshot contains every requested selector."""
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
        resolved_ref: str | None,
        commit: str,
        reference_kind: str | None,
    ) -> GuidelineCacheSnapshot:
        """Atomically publish one union sparse checkout and metadata entry."""
        try:
            location = validate_source_location(location)
            validated_locations = tuple(validate_source_location(item) for item in locations)
        except (TypeError, ValueError):
            raise ValueError("cache source location is unsafe or inconsistent") from None
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
        with self._metadata_lock():
            payload = self._load_metadata()
            existing = next(
                (item for item in self._entries(payload) if item.get("key") == key), None
            )
            if existing is not None and isinstance(existing.get("paths"), list):
                paths.update(str(item) for item in cast(list[object], existing["paths"]))
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
                    self._remove(entry_path)
                os.replace(checkout_root, entry_path)
            else:
                if entry_path.exists():
                    self._validate_snapshot_tree(entry_path)
                else:
                    entry_path.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    checkout_root,
                    entry_path,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"),
                    symlinks=True,
                )
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
            }
            payload["entries"] = [
                item for item in self._entries(payload) if item.get("key") != key
            ] + [entry]
            self._cleanup_payload(payload)
            self._save_metadata(payload)
            snapshot = self._snapshot_for_entry(location, entry)
            if snapshot is None:
                raise ValueError("stored guideline snapshot could not be validated")
            return snapshot

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
