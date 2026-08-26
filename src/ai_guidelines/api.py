"""The supported automation facade for :mod:`ai_guidelines`.

Only the functions in this module are part of the stable library API.  The
implementation modules remain available for internal composition, but their
helpers and metadata formats are not public contracts.
"""

from __future__ import annotations

from pathlib import Path

from ai_guidelines.cache import MaterializedGuidelineCache as _MaterializedGuidelineCache
from ai_guidelines.discovery import DiscoveryResult, discover_guidelines
from ai_guidelines.locations import SourceLocation, parse_location
from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.manifest import load_manifest, save_manifest
from ai_guidelines.models import GuidelinesLock, GuidelinesManifest
from ai_guidelines.sync import GuidelinesSyncResult, sync_manifest
from ai_guidelines.update import (
    GuidelineUpdateResult,
    OutdatedReport,
    UpdatePlan,
    apply_update_plan,
    build_update_plan,
    inspect_outdated,
)

__all__ = [
    "DiscoveryResult",
    "GuidelineUpdateResult",
    "GuidelinesLock",
    "GuidelinesManifest",
    "GuidelinesSyncResult",
    "OutdatedReport",
    "SourceLocation",
    "UpdatePlan",
    "apply_update_plan",
    "build_update_plan",
    "discover_guidelines",
    "inspect_outdated",
    "load_lockfile",
    "load_manifest",
    "parse_location",
    "save_lockfile",
    "save_manifest",
    "sync_manifest",
    "cache_dir",
    "cache_size",
    "cache_clean",
    "cache_prune",
]


def _cache(cache: _MaterializedGuidelineCache | None = None) -> _MaterializedGuidelineCache:
    return cache or _MaterializedGuidelineCache()


def cache_dir(cache: _MaterializedGuidelineCache | None = None) -> Path:
    """Return the disposable source-cache directory."""
    return _cache(cache).cache_dir


def cache_size(cache: _MaterializedGuidelineCache | None = None) -> int:
    """Return cache size in bytes."""
    return _cache(cache).size()


def cache_clean(cache: _MaterializedGuidelineCache | None = None) -> int:
    """Remove all cache snapshots and return the number removed."""
    return _cache(cache).clear_all()


def cache_prune(cache: _MaterializedGuidelineCache | None = None) -> int:
    """Remove expired cache snapshots and return the number removed."""
    return _cache(cache).cleanup()
