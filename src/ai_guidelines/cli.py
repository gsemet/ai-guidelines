"""Standalone ``guidelines`` command-line interface."""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import click

from ai_guidelines.cache import DiscoveryCache, MaterializedGuidelineCache
from ai_guidelines.discovery import DiscoveryError, discover_guidelines, is_guideline_file
from ai_guidelines.fetch import SourceFetcher, SourceFetchError
from ai_guidelines.locations import LocationParseError, parse_location, with_source_path
from ai_guidelines.manifest import load_manifest, save_manifest
from ai_guidelines.models import GuidelineDeclaration, GuidelinesManifest
from ai_guidelines.paths import resolve_guideline_target, resolve_target_path
from ai_guidelines.reporter import (
    print_outdated_report,
    print_sync_report,
    print_update_plan,
    print_update_result,
)
from ai_guidelines.sync import FrozenSyncError, SyncError, sync_manifest
from ai_guidelines.update import (
    GuidelineUpdateError,
    apply_update_plan,
    build_update_plan,
    inspect_outdated,
)


def _project() -> Path:
    return Path.cwd().resolve()


def _manifest(project: Path) -> GuidelinesManifest:
    path = project / "guidelines.yml"
    return load_manifest(path) if path.exists() else GuidelinesManifest().bind_source_base(project)


def _error(error: Exception) -> click.ClickException:
    return click.ClickException(str(error))


@click.group(name="guidelines", context_settings={"help_option_names": ["-h", "--help"]})
def guidelines() -> None:
    """Manage reusable project-owned Markdown guidelines."""


@guidelines.command("sync")
@click.option("--dry-run/--no-dry-run", default=False, help="Preview without writing files.")
@click.option("--frozen/--no-frozen", default=False, help="Replay only complete locked state.")
def sync(*, dry_run: bool, frozen: bool) -> None:
    """Resolve and synchronize declarations in guidelines.yml."""
    try:
        print_sync_report(sync_manifest(_project(), dry_run=dry_run, frozen=frozen))
    except (FileNotFoundError, FrozenSyncError, SyncError, OSError, ValueError) as exc:
        raise _error(exc) from None


def _files(root: Path) -> list[Path]:
    if root.is_file():
        return [] if root.is_symlink() or not is_guideline_file(root) else [root]
    if not root.is_dir() or root.is_symlink():
        return []
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or not is_guideline_file(path):
            continue
        with suppress(OSError):
            if path.resolve().is_relative_to(root.resolve()):
                result.append(path)
    return sorted(result)


def _target_is_unreadable(root: Path) -> bool:
    """Return whether a configured target cannot be safely inspected."""
    try:
        if root.is_symlink() or not root.exists() or not root.is_dir():
            return True
        return not os.access(root, os.R_OK | os.X_OK)
    except OSError:
        return True


@guidelines.command("list")
def list_guidelines() -> None:
    """List guideline files in every configured target without following symlinks."""
    project = _project()
    manifest = _manifest(project)
    lock = project / "guidelines.lock.json"
    from ai_guidelines.lockfile import load_lockfile

    document = load_lockfile(lock) if lock.exists() else None
    target_paths: set[str] = set()
    locked_targets: dict[str, list[str]] = {}
    if document:
        for entry in document.guidelines:
            locked_target = entry.normalized_target_path or "."
            target_paths.add(locked_target)
            locked_targets.setdefault(locked_target, []).extend(
                record.normalized_target_path for record in entry.files
            )
    for declaration in manifest.guidelines:
        target: str | None = (
            declaration.normalized_target_path or manifest.normalized_default_guidelines_path
        )
        target_paths.add(
            target or resolve_guideline_target(project).relative_to(project).as_posix()
        )
    if not target_paths:
        target_paths.add(resolve_guideline_target(project).relative_to(project).as_posix())
    locked: dict[str, tuple[str, str | None]] = {}
    active_names = {
        declaration.alias or declaration.display_name for declaration in manifest.guidelines
    }
    declared_targets: set[str] = set()
    for declaration in manifest.guidelines:
        configured = (
            declaration.normalized_target_path or manifest.normalized_default_guidelines_path
        )
        declared_targets.add(
            configured or resolve_guideline_target(project).relative_to(project).as_posix()
        )
    if document:
        for entry in document.guidelines:
            for record in entry.files:
                locked[record.normalized_target_path] = (entry.name, record.sha256)
    emitted = False
    found_any_file = False
    for target_path in sorted(target_paths):
        try:
            root = resolve_target_path(project, target_path)
        except (OSError, ValueError):
            click.echo(f"Guidelines in {target_path}")
            click.echo("Target is unreadable.")
            continue
        if _target_is_unreadable(root):
            click.echo(f"Guidelines in {target_path}")
            click.echo("Target is unreadable.")
            continue
        files = _files(root)
        found_any_file = found_any_file or bool(files)
        click.echo(f"Guidelines in {target_path}")
        expected = locked_targets.get(target_path, [])
        if not files and not expected:
            click.echo("No guideline files found in target.")
        for path in files:
            emitted = True
            relative = path.relative_to(project).as_posix()
            name_hash = locked.get(relative)
            ownership = "source-backed" if name_hash else "manual"
            if name_hash and name_hash[0] not in active_names:
                ownership = "historical"
            state = "local"
            if name_hash:
                try:
                    state = (
                        "up-to-date"
                        if hashlib.sha256(path.read_bytes()).hexdigest() == name_hash[1]
                        else "locally edited"
                    )
                except OSError:
                    state = "unreadable"
            click.echo(f"{path.name}\t{ownership}\t{state}\t{relative}")
        for relative, (_name, _expected) in sorted(locked.items()):
            if target_path == "." or relative.startswith(f"{target_path}/"):
                candidate = project / relative
            else:
                continue
            if not candidate.exists() or candidate.is_symlink():
                emitted = True
                click.echo(f"{Path(relative).name}\tsource-backed\tunreadable\t{relative}")
    if not emitted and not found_any_file:
        click.echo("No guideline files found in any target.")


@guidelines.command("search")
@click.argument("location", required=False)
@click.argument("filename_query", required=False)
@click.option("--query", "query_option", help="Filename-stem glob for configured sources.")
@click.option("--refresh", is_flag=True, help="Refresh discovery cache.")
@click.option("--no-cache", is_flag=True, help="Bypass discovery cache.")
def search(
    location: str | None,
    filename_query: str | None,
    query_option: str | None,
    *,
    refresh: bool,
    no_cache: bool,
) -> None:
    """Search a location, or all sources declared in guidelines.yml."""
    if location and query_option:
        raise click.ClickException("--query cannot be used with a location")
    project = _project()
    try:
        declarations: list[Any]
        locations: list[Any]
        queries: list[str | None]
        if location:
            declarations, locations, queries = (
                [None],
                [parse_location(location, base_dir=project)],
                [filename_query],
            )
        else:
            document = _manifest(project)
            declarations = list(document.guidelines)
            locations = [
                parse_location(item.source, ref=item.ref, base_dir=project) for item in declarations
            ]
            queries = [query_option] * len(locations)
        cache = DiscoveryCache()
        for _declaration, source, query in zip(declarations, locations, queries, strict=True):
            revision = source.requested_ref or (
                "working-tree" if source.source_type == "local" else None
            )
            found = cache.read(
                source,
                revision=revision,
                root=source.local_path,
                refresh=refresh,
                bypass=no_cache,
            )
            if found is None:
                with SourceFetcher().acquire(source) as acquired:
                    found = discover_guidelines(acquired)
                if not no_cache:
                    cache.write(source, found, revision=revision)
            for candidate in found.files:
                if query is None or any(
                    fnmatchcase(value, query)
                    for value in (candidate.filename, candidate.suffix_stripped_name)
                ):
                    installed = "no"
                    target_root = resolve_guideline_target(project)
                    if _declaration is not None:
                        lockfile = project / "guidelines.lock.json"
                        previous = None
                        if lockfile.exists():
                            from ai_guidelines.lockfile import load_lockfile

                            previous = load_lockfile(lockfile).find_entry(
                                _declaration, base_dir=project
                            )
                        configured = _declaration.normalized_target_path or (
                            previous.normalized_target_path if previous else None
                        )
                        if configured:
                            target_root = resolve_target_path(project, configured)
                    target = target_root / candidate.source_path
                    if target.is_file() and not target.is_symlink():
                        installed = "yes"
                    click.echo(
                        f"{candidate.filename}\t{source.canonical_source}\t"
                        f"{candidate.source_path}\t{installed}"
                    )
    except (LocationParseError, DiscoveryError, OSError, TypeError, ValueError) as exc:
        raise _error(exc) from None


def _literal(value: str) -> str:
    value = value.strip("/\\").replace("\\", "/")
    if value.endswith(".guideline.md"):
        return f"{value[: -len('.guideline.md')]}.guidelines.md"
    return value if value.endswith(".guidelines.md") else value + ".guidelines.md"


@guidelines.command("add")
@click.argument("location")
@click.argument("pattern", required=False)
@click.option("--ref")
@click.option("--target-path")
@click.option("--alias")
@click.option("--path", "paths", multiple=True, help="Select an exact source-relative path.")
def add(
    location: str,
    pattern: str | None,
    ref: str | None,
    target_path: str | None,
    alias: str | None,
    paths: tuple[str, ...],
) -> None:
    """Add a normalized declaration and synchronize it immediately."""
    project = _project()
    try:
        document = _manifest(project)
        parsed = parse_location(location, ref=ref, base_dir=project)
        if pattern is not None and paths:
            raise click.ClickException("PATTERN and --path cannot be combined")
        exact = pattern is not None and (
            "/" in pattern
            or "\\" in pattern
            or pattern.endswith((".guideline.md", ".guidelines.md"))
        )
        path = _literal(pattern) if exact and pattern else None
        if path:
            if path and parsed.source_type == "local":
                local_path = (parsed.local_path or project).joinpath(*path.split("/"))
                parsed = parsed.model_copy(
                    update={
                        "local_path": local_path,
                        "relative_path": path,
                        "kind": "file",
                        "canonical_source": local_path.resolve(strict=False).as_posix(),
                    }
                )
            elif path:
                parsed = with_source_path(parsed, path)
        declaration = GuidelineDeclaration(
            source=location,
            ref=ref,
            path=path,
            pattern=None if exact else pattern,
            target_path=target_path,
            alias=alias,
            paths=list(paths) or None,
        ).bind_source_base(project)
        existing = next(
            (
                item
                for item in document.guidelines
                if item.canonical_source_for(project) == declaration.canonical_source_for(project)
            ),
            None,
        )
        if existing is None:
            document.guidelines.append(declaration)
        else:
            document.guidelines[document.guidelines.index(existing)] = declaration
        save_manifest(project / "guidelines.yml", document)
        sync_manifest(project)
        click.echo(
            f"Guidelines source {'added' if existing is None else 'updated'}: "
            f"{alias or parsed.display_name}"
        )
    except (LocationParseError, OSError, SyncError, TypeError, ValueError) as exc:
        raise _error(exc) from None


@guidelines.command("remove")
@click.argument("identifier")
def remove(identifier: str) -> None:
    """Remove a declaration while preserving installed files."""
    project = _project()
    path = project / "guidelines.yml"
    if not path.exists():
        raise click.ClickException("No guidelines.yml manifest exists.")
    try:
        document = _manifest(project)
        matches = [
            item
            for item in document.guidelines
            if (
                item.alias == identifier
                or item.display_name.casefold() == identifier.casefold()
                or item.canonical_source_for(project)
                == parse_location(identifier, base_dir=project).canonical_source
            )
        ]
    except (LocationParseError, OSError, TypeError, ValueError):
        matches = [
            item
            for item in _manifest(project).guidelines
            if item.alias == identifier or item.display_name.casefold() == identifier.casefold()
        ]
        document = _manifest(project)
    if len(matches) != 1:
        raise click.ClickException(
            "No guideline source matched the identifier."
            if not matches
            else "Ambiguous guideline identifier."
        )
    removed = matches[0]
    document.guidelines.remove(removed)
    save_manifest(path, document)
    lock_path = project / "guidelines.lock.json"
    if lock_path.exists():
        from ai_guidelines.lockfile import load_lockfile, save_lockfile

        lock_document = load_lockfile(lock_path)
        lock_document.guidelines = [
            entry
            for entry in lock_document.guidelines
            if not entry.matches(removed, base_dir=project)
        ]
        save_lockfile(lock_path, lock_document)
    click.echo(f"Guidelines source removed: {matches[0].display_name}")
    click.echo("No guideline files were deleted or modified.")


@guidelines.command("outdated")
def outdated() -> None:
    """Report available revisions without changing project state."""
    try:
        print_outdated_report(inspect_outdated(_project()))
    except (FileNotFoundError, GuidelineUpdateError, SourceFetchError, OSError, ValueError) as exc:
        raise _error(exc) from None


@guidelines.command("update")
@click.option("--yes", is_flag=True)
@click.option("--interactive", "interactive", is_flag=True, default=False)
@click.option("--dry-run/--no-dry-run", default=False)
def update(*, yes: bool, interactive: bool, dry_run: bool) -> None:
    """Plan and apply updates to moving sources."""
    try:
        plan = build_update_plan(_project()).model_copy(update={"dry_run": dry_run})
        print_update_plan(plan)
        if dry_run or not plan.has_changes:
            if not plan.has_changes and not dry_run:
                click.echo("No guideline updates are available.")
            return
        if interactive and not yes and not click.confirm("Apply these changes?", default=False):
            click.echo("No changes applied.")
            return
        print_update_result(apply_update_plan(_project(), plan))
    except (FileNotFoundError, GuidelineUpdateError, SourceFetchError, OSError, ValueError) as exc:
        raise _error(exc) from None


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cache() -> None:
    """Maintain the disposable guideline cache."""


def _cache() -> MaterializedGuidelineCache:
    return MaterializedGuidelineCache()


@cache.command("clean")
def cache_clean() -> None:
    """Remove all disposable snapshots."""
    click.echo(f"Removed {_cache().clear_all()} cache entries.")


@cache.command("prune")
def cache_prune() -> None:
    """Remove expired disposable snapshots."""
    click.echo(f"Pruned {_cache().cleanup()} expired cache entries.")


@cache.command("dir")
def cache_dir() -> None:
    """Print the disposable cache directory."""
    click.echo(_cache().cache_dir)


@cache.command("size")
def cache_size() -> None:
    """Print disposable cache size."""
    click.echo(f"Cache size: {_cache().size()} bytes")


guidelines.add_command(cache)
