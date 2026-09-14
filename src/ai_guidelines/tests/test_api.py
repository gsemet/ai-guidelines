"""Tests for the deliberately small stable Python facade."""

from __future__ import annotations

import inspect
from pathlib import Path

import ai_guidelines
import ai_guidelines.api as api


def test_facade_exports_only_supported_capabilities() -> None:
    """Expose only the supported stable facade names."""
    expected = {
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
        "cache_clean",
        "cache_dir",
        "cache_prune",
        "cache_size",
        "discover_guidelines",
        "inspect_outdated",
        "load_lockfile",
        "load_manifest",
        "parse_location",
        "save_lockfile",
        "save_manifest",
        "sync_manifest",
    }
    assert set(api.__all__) == expected
    assert "MaterializedGuidelineCache" not in api.__all__
    assert "MaterializedGuidelineCache" not in ai_guidelines.__all__
    for name in expected:
        assert hasattr(ai_guidelines, name) or hasattr(api, name)


def test_cache_services_do_not_require_a_project_manifest() -> None:
    """Exercise cache facade helpers without loading a project manifest."""

    class FakeCache:
        """Provide the cache methods consumed by the facade helpers."""

        cache_dir = Path("/tmp/ai-guidelines-test-cache")

        def size(self) -> int:
            """Return a deterministic cache size."""
            return 17

        def clear_all(self) -> int:
            """Return a deterministic number of removed entries."""
            return 3

        def cleanup(self) -> int:
            """Return a deterministic number of pruned entries."""
            return 2

    cache = FakeCache()
    assert api.cache_dir(cache) == cache.cache_dir
    assert api.cache_size(cache) == 17
    assert api.cache_clean(cache) == 3
    assert api.cache_prune(cache) == 2
    assert all(
        inspect.signature(getattr(api, name)).return_annotation is not inspect.Signature.empty
        for name in ("cache_dir", "cache_size", "cache_clean", "cache_prune")
    )


def test_facade_imports_every_supported_symbol() -> None:
    """Keep package-level exports identical to the public API module."""
    for name in api.__all__:
        assert getattr(ai_guidelines, name) is getattr(api, name)
