"""Tests for disposable discovery and materialized source caches."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ai_guidelines import cache as cache_module
from ai_guidelines.cache import DiscoveryCache, MaterializedGuidelineCache
from ai_guidelines.discovery import DiscoveredGuideline, DiscoveryResult
from ai_guidelines.locations import SourceLocation, parse_location, with_source_path


def _discovered(path: Path, source_path: str) -> DiscoveredGuideline:
    return DiscoveredGuideline(
        path=path,
        source_path=source_path,
        suffix_stripped_name=Path(source_path).name.split(".guideline", 1)[0],
    )


def test_discovery_cache_uses_platformdirs_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = tmp_path / "platform-cache"
    monkeypatch.setattr(
        cache_module.platformdirs, "user_cache_dir", lambda app_name: str(cache_root)
    )
    location = parse_location("github/example/repo/guidelines/")
    cache = DiscoveryCache()

    assert cache.cache_dir == cache_root / "guidelines"
    assert cache.cache_path(location, revision="main") != cache.cache_path(location, revision="v2")
    assert cache.cache_path(
        parse_location("github/example/repo/other/"), revision="main"
    ) != cache.cache_path(location, revision="main")


def test_discovery_cache_reuses_then_expires_entries(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)]
    cache = DiscoveryCache(cache_dir=tmp_path / "discovery", clock=lambda: current[0])
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "team.guidelines.md"
    source_file.write_text("# Team\n", encoding="utf-8")
    location = parse_location(str(source))
    result = DiscoveryResult(files=[_discovered(source_file, "team.guidelines.md")])

    cache.write(location, result, revision="working-tree")
    current[0] += timedelta(minutes=9, seconds=59)
    assert cache.read(location, revision="working-tree", root=source) is not None
    current[0] += timedelta(seconds=1)
    assert cache.read(location, revision="working-tree", root=source) is None
    assert not cache.cache_path(location, revision="working-tree").exists()


def test_discovery_cache_refresh_bypass_corruption_and_safe_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "team.guidelines.md"
    source_file.write_text("team", encoding="utf-8")
    location = parse_location(str(source))
    cache = DiscoveryCache(cache_dir=tmp_path / "discovery")
    result = DiscoveryResult(files=[_discovered(source_file, "team.guidelines.md")])
    cache.write(location, result, revision="working-tree")

    assert cache.read(location, revision="working-tree", refresh=True) is None
    cache.write(location, result, revision="working-tree")
    assert cache.read(location, revision="working-tree", bypass=True) is None
    path = cache.cache_path(location, revision="working-tree")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["files"][0]["source_path"] = "../../outside.guidelines.md"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.read(location, revision="working-tree", root=source) is None
    assert not path.exists()


def test_discovery_cache_rejects_credential_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="credentials"):
        DiscoveryCache(cache_dir=tmp_path).cache_key("https://user:secret@example.com/repo.git")


def test_cache_identity_boundaries_reject_inconsistent_locations(tmp_path: Path) -> None:
    remote = parse_location("https://example.com/team/repo#main:guidelines/")
    tampered_remote = remote.model_copy(update={"repository": "https://example.com/other/repo"})
    discovery_cache = DiscoveryCache(cache_dir=tmp_path / "discovery")
    materialized_cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    with pytest.raises(ValueError, match="source|location|safe"):
        discovery_cache.cache_key(tampered_remote)
    with pytest.raises(ValueError, match="source|location|safe"):
        materialized_cache.cache_path(tampered_remote, "a" * 40)

    source = tmp_path / "source"
    source.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    local = parse_location(str(source))
    tampered_local = local.model_copy(update={"local_path": other})
    with pytest.raises(ValueError, match="source|location|safe"):
        discovery_cache.cache_key(tampered_local)


def test_materialized_cache_merges_paths_for_one_revision(tmp_path: Path) -> None:
    checkout_a = tmp_path / "checkout-a"
    (checkout_a / "guidelines").mkdir(parents=True)
    (checkout_a / "guidelines" / "a.guidelines.md").write_text("a", encoding="utf-8")
    checkout_b = tmp_path / "checkout-b"
    (checkout_b / "guidelines").mkdir(parents=True)
    (checkout_b / "guidelines" / "b.guidelines.md").write_text("b", encoding="utf-8")
    repository = parse_location("https://example.com/team/repo#main:guidelines/")
    first = with_source_path(repository, "guidelines/a.guidelines.md")
    second = with_source_path(repository, "guidelines/b.guidelines.md")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    cache.write_snapshot(
        repository,
        checkout_a,
        [first],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )
    cache.write_snapshot(
        repository,
        checkout_b,
        [second],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )

    first_snapshot = cache.lookup(first)
    second_snapshot = cache.lookup(second)
    assert first_snapshot is not None and second_snapshot is not None
    assert first_snapshot.root == second_snapshot.root
    assert (first_snapshot.root / "guidelines/a.guidelines.md").read_text() == "a"
    assert (second_snapshot.root / "guidelines/b.guidelines.md").read_text() == "b"
    assert cache.cache_path(first, "a" * 40) == cache.cache_path(second, "a" * 40)


def test_materialized_cache_missing_path_is_a_miss_without_removing_entry(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "guidelines").mkdir(parents=True)
    (checkout / "guidelines" / "first.guidelines.md").write_text("first", encoding="utf-8")
    repository = parse_location("https://example.com/team/repo#main:guidelines/")
    first = with_source_path(repository, "guidelines/first.guidelines.md")
    missing = with_source_path(repository, "guidelines/missing.guidelines.md")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")
    cache.write_snapshot(
        repository, checkout, [first], resolved_ref="main", commit="a" * 40, reference_kind="branch"
    )

    assert cache.lookup(missing) is None
    assert cache.cache_path(first, "a" * 40).is_dir()
    assert cache.lookup(first) is not None


def test_materialized_cache_root_misses_when_required_selector_is_absent(tmp_path: Path) -> None:
    """A cached repository root does not satisfy a newly added sparse selector."""
    checkout = tmp_path / "checkout"
    first_path = checkout / "guidelines" / "first.guidelines.md"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("first", encoding="utf-8")
    repository = parse_location("https://example.com/team/repo#main")
    first = with_source_path(repository, "guidelines/first.guidelines.md")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    cache.write_snapshot(
        repository,
        checkout,
        [first],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )

    assert cache.lookup(repository, required_paths=["guidelines/missing"]) is None
    assert cache.lookup(repository, required_paths=["guidelines/first"]) is not None
    assert cache.cache_path(repository, "a" * 40).is_dir()


def test_materialized_cache_frozen_lookup_does_not_create_cache_state(tmp_path: Path) -> None:
    """Frozen cache validation reads without creating a lock or cache directory."""
    cache_dir = tmp_path / "materialized"
    location = parse_location("https://example.com/team/repo#main:guidelines/")
    cache = MaterializedGuidelineCache(cache_dir=cache_dir)

    assert cache.lookup(location, touch=False) is None
    assert not cache_dir.exists()


def test_materialized_cache_excludes_git_metadata_for_external_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "guidelines").mkdir(parents=True)
    (checkout / "guidelines" / "team.guideline.md").write_text("team", encoding="utf-8")
    (checkout / ".git").mkdir()
    (checkout / ".git" / "config").write_text("private", encoding="utf-8")
    location = parse_location("https://example.com/team/repo#main:guidelines/")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    cache.write_snapshot(
        location,
        checkout,
        [location],
        resolved_ref="main",
        commit="b" * 40,
        reference_kind="branch",
    )

    entry = cache.cache_path(location, "b" * 40)
    assert (entry / "guidelines/team.guideline.md").exists()
    assert not (entry / ".git").exists()


def test_materialized_cache_rejects_escaping_source_symlink(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "guidelines").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.guidelines.md"
    secret.write_text("secret", encoding="utf-8")
    (checkout / "guidelines" / "escape.guidelines.md").symlink_to(secret)
    location = parse_location("https://example.com/team/repo#main:guidelines/")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    with pytest.raises(ValueError, match="symlink|escape|source"):
        cache.write_snapshot(
            location,
            checkout,
            [location],
            resolved_ref="main",
            commit="b" * 40,
            reference_kind="branch",
        )

    assert not cache.cache_path(location, "b" * 40).exists()
    assert not cache.metadata_path.exists()


def test_cache_owned_checkout_is_promoted_with_git_metadata(tmp_path: Path) -> None:
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")
    location = parse_location("https://example.com/team/repo#main:guidelines/")
    checkout = cache.checkout_path(location)
    (checkout / ".git").mkdir(parents=True)
    (checkout / ".git" / "config").write_text("metadata", encoding="utf-8")
    (checkout / "guidelines").mkdir(parents=True)
    (checkout / "guidelines" / "team.guideline.md").write_text("team", encoding="utf-8")

    cache.write_snapshot(
        location,
        checkout,
        [location],
        resolved_ref="main",
        commit="c" * 40,
        reference_kind="branch",
    )

    entry = cache.cache_path(location, "c" * 40)
    assert (entry / ".git" / "config").read_text() == "metadata"
    assert not checkout.exists()


def test_cache_keys_are_credential_free_and_pending_checkouts_unique(tmp_path: Path) -> None:
    location = parse_location("https://example.com/team/repo#main:guidelines/")
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")

    first = cache.checkout_path(location)
    second = cache.checkout_path(location)

    assert first != second
    assert first.name.startswith("example.com_team_repo_main_pending_")
    identity_digest = hashlib.sha256(b"example.com/team/repo").hexdigest()
    assert cache.cache_key(location, "a" * 40) == (
        "example.com_team_repo__" + identity_digest + "__" + "a" * 40
    )


def test_materialized_cache_keys_isolate_repository_names_that_sanitize_alike(
    tmp_path: Path,
) -> None:
    cache = MaterializedGuidelineCache(cache_dir=tmp_path / "materialized")
    slash_repository = parse_location("https://example.com/a/b")
    underscore_repository = parse_location("https://example.com/a_b")

    assert cache.repository_identity(slash_repository) != cache.repository_identity(
        underscore_repository
    )
    assert cache.cache_path(slash_repository, "a" * 40) != cache.cache_path(
        underscore_repository, "a" * 40
    )


def test_cache_refresh_and_idle_eviction_have_separate_ttls(tmp_path: Path) -> None:
    now = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "team.guidelines.md").write_text("team", encoding="utf-8")
    location = parse_location("https://example.com/team/repo#main:team.guidelines.md")
    cache = MaterializedGuidelineCache(
        cache_dir=tmp_path / "materialized",
        refresh_ttl=timedelta(minutes=10),
        eviction_ttl=timedelta(days=10),
        clock=lambda: now[0],
    )
    cache.write_snapshot(
        location,
        checkout,
        [location],
        resolved_ref="main",
        commit="b" * 40,
        reference_kind="branch",
    )

    now[0] += timedelta(minutes=10)
    assert cache.lookup(location) is None
    assert cache.cache_path(location, "b" * 40).exists()
    now[0] += timedelta(days=10, seconds=1)
    assert cache.cleanup() == 1
    assert not cache.cache_path(location, "b" * 40).exists()


def test_concurrent_writers_merge_metadata_and_release_lock(tmp_path: Path) -> None:
    cache_dir = tmp_path / "materialized"
    locations = [
        parse_location("https://example.com/team/repo#main:guidelines/first.guideline.md"),
        parse_location("https://other.example/team/repo#main:guidelines/other.guideline.md"),
    ]
    checkouts: list[Path] = []
    for index, location in enumerate(locations):
        checkout = tmp_path / f"checkout-{index}"
        source = checkout / location.relative_path
        source.parent.mkdir(parents=True)
        source.write_text(str(index), encoding="utf-8")
        checkouts.append(checkout)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def writer(location: SourceLocation, checkout: Path, commit: str) -> None:
        try:
            barrier.wait(timeout=5)
            MaterializedGuidelineCache(cache_dir=cache_dir).write_snapshot(
                location,
                checkout,
                [location],
                resolved_ref="main",
                commit=commit,
                reference_kind="branch",
            )
        except Exception as error:  # pragma: no cover - assertion reports details
            errors.append(error)

    threads = [
        threading.Thread(target=writer, args=(locations[0], checkouts[0], "a" * 40)),
        threading.Thread(target=writer, args=(locations[1], checkouts[1], "b" * 40)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    cache = MaterializedGuidelineCache(cache_dir=cache_dir)
    payload = yaml.safe_load(cache.metadata_path.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 2
    assert cache.lookup(locations[0]) is not None
    assert cache.lookup(locations[1]) is not None
    assert cache.metadata_lock_path.exists()


def test_sidecar_lock_is_released_after_metadata_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "materialized"
    location = parse_location("https://example.com/team/repo#main:guidelines/team.guideline.md")
    failed_checkout = tmp_path / "failed"
    (failed_checkout / location.relative_path).parent.mkdir(parents=True)
    (failed_checkout / location.relative_path).write_text("failed", encoding="utf-8")
    successful_checkout = tmp_path / "successful"
    (successful_checkout / location.relative_path).parent.mkdir(parents=True)
    (successful_checkout / location.relative_path).write_text("successful", encoding="utf-8")
    cache = MaterializedGuidelineCache(cache_dir=cache_dir)
    original_replace = cache_module.os.replace
    failure = True

    def fail_once(source: str | Path, destination: str | Path) -> None:
        nonlocal failure
        if Path(destination) == cache.metadata_path and failure:
            failure = False
            raise OSError("simulated metadata publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(cache_module.os, "replace", fail_once)
    with pytest.raises(OSError, match="simulated"):
        cache.write_snapshot(
            location,
            failed_checkout,
            [location],
            resolved_ref="main",
            commit="a" * 40,
            reference_kind="branch",
        )

    recovered = MaterializedGuidelineCache(cache_dir=cache_dir)
    recovered.write_snapshot(
        location,
        successful_checkout,
        [location],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )
    assert recovered.lookup(location) is not None
    assert (
        recovered.cache_path(location, "a" * 40) / location.relative_path
    ).read_text() == "successful"
