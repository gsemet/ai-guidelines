"""Standalone ``guidelines`` command-line interface."""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import click

import ai_guidelines.atomic as atomic
from ai_guidelines.cache import DiscoveryCache, MaterializedGuidelineCache
from ai_guidelines.discovery import (
    DiscoveryError,
    discover_guidelines,
    filter_guidelines,
    is_guideline_file,
)
from ai_guidelines.fetch import SourceFetcher, SourceFetchError
from ai_guidelines.locations import LocationParseError, parse_location, with_source_path
from ai_guidelines.manifest import load_manifest, save_manifest
from ai_guidelines.models import GuidelineDeclaration, GuidelinesManifest
from ai_guidelines.paths import (
    declaration_location,
    operation_lock_path,
    resolve_guideline_target,
    resolve_target_path,
    select_guideline_target,
    target_is_folder,
)
from ai_guidelines.reconcile import ReconciliationJournal
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
    """Return the current working directory as the consumer project root."""
    return Path.cwd().resolve()


def _manifest(project: Path) -> GuidelinesManifest:
    """Load a project manifest or return an empty bound manifest."""
    path = project / "guidelines.yml"
    return load_manifest(path) if path.exists() else GuidelinesManifest().bind_source_base(project)


def _error(error: Exception) -> click.ClickException:
    """Convert a sanitized service exception into a Click error."""
    return click.ClickException(str(error))


@click.group(name="guidelines", context_settings={"help_option_names": ["-h", "--help"]})
def guidelines() -> None:
    """Manage reusable project-owned Markdown guidelines.

    .. versionchanged:: 0.2.0
        Commands now share the source, lock, cache, and target safety contracts
        exposed by the Python API.
    """


@guidelines.command("sync")
@click.option("--dry-run/--no-dry-run", default=False, help="Preview without writing files.")
@click.option("--frozen/--no-frozen", default=False, help="Replay only complete locked state.")
def sync(*, dry_run: bool, frozen: bool) -> None:
    """Resolve and synchronize declarations in ``guidelines.yml``.

    .. versionchanged:: 0.2.0
        ``--frozen`` performs exact replay without source resolution,
        acquisition, or writes.

    Args:
        dry_run:
            Preview source and file actions without writing project state.
        frozen:
            Require complete lock and cache state for exact replay.
    """
    try:
        print_sync_report(sync_manifest(_project(), dry_run=dry_run, frozen=frozen))
    except (FileNotFoundError, FrozenSyncError, SyncError, OSError, ValueError) as exc:
        raise _error(exc) from None


def _files(root: Path) -> list[Path]:
    """Return safe guideline files below a target without following symlinks.

    Args:
        root:
            File or directory to inspect.

    Returns:
        Sorted regular guideline files contained by ``root``.
    """
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
        if root.is_symlink() or not root.exists():
            return True
        if root.is_file():
            return not os.access(root, os.R_OK)
        if not root.is_dir():
            return True
        return not os.access(root, os.R_OK | os.X_OK)
    except OSError:
        return True


@guidelines.command("list")
def list_guidelines() -> None:
    """List guideline files in every configured target without following symlinks.

    .. versionchanged:: 0.2.0
        Output distinguishes source-backed, historical, manual, and locally
        edited files.
    """
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
        previous = document.find_entry(declaration, base_dir=project) if document else None
        try:
            target = select_guideline_target(
                project,
                declaration=declaration,
                lock_entry=previous,
                default_target_path=manifest.normalized_default_guidelines_path,
            )
            target_paths.add(target.relative_to(project).as_posix())
        except (OSError, ValueError):
            target_paths.add(
                declaration.normalized_target_path
                or manifest.normalized_default_guidelines_path
                or resolve_guideline_target(project).relative_to(project).as_posix()
            )
    if not target_paths:
        target_paths.add(resolve_guideline_target(project).relative_to(project).as_posix())
    locked: dict[str, tuple[str, str | None]] = {}
    active_names = {
        declaration.alias or declaration.display_name for declaration in manifest.guidelines
    }
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
            if (
                target_path == "."
                or relative == target_path
                or relative.startswith(f"{target_path}/")
            ):
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
    """Search a location, or all sources declared in ``guidelines.yml``.

    .. versionchanged:: 0.2.0
        Search supports refresh and no-cache controls for discovery metadata.

    Args:
        location:
            Optional source expression to search directly.
        filename_query:
            Optional filename-stem glob for a direct source search.
        query_option:
            Optional filename-stem glob for configured sources.
        refresh:
            Invalidate the relevant discovery-cache entry before searching.
        no_cache:
            Bypass discovery-cache reads and writes.

    Examples:
        From a shell, search configured sources without using discovery cache:

        .. code-block:: console

            $ guidelines search --no-cache
    """
    if location and query_option:
        raise click.ClickException("--query cannot be used with a location")
    project = _project()
    try:
        declarations: list[Any]
        locations: list[Any]
        queries: list[str | None]
        manifest: GuidelinesManifest | None = None
        lock_document: Any | None = None
        if location:
            declarations, locations, queries = (
                [None],
                [parse_location(location, base_dir=project)],
                [filename_query],
            )
        else:
            manifest = _manifest(project)
            declarations = list(manifest.guidelines)
            locations = [declaration_location(item, project) for item in declarations]
            queries = [query_option] * len(locations)
            if (project / "guidelines.lock.json").exists():
                from ai_guidelines.lockfile import load_lockfile

                lock_document = load_lockfile(project / "guidelines.lock.json")
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
            if _declaration is not None:
                found = filter_guidelines(
                    found,
                    pattern=_declaration.pattern,
                    paths=_declaration.paths,
                )
            for candidate in found.files:
                if query is None or any(
                    fnmatchcase(value, query)
                    for value in (candidate.filename, candidate.suffix_stripped_name)
                ):
                    installed = "no"
                    previous = (
                        lock_document.find_entry(_declaration, base_dir=project)
                        if _declaration is not None and lock_document is not None
                        else None
                    )
                    target_root = select_guideline_target(
                        project,
                        declaration=_declaration,
                        lock_entry=previous,
                        default_target_path=(
                            manifest.normalized_default_guidelines_path
                            if manifest is not None
                            else None
                        ),
                    )
                    target_relative = target_root.relative_to(project).as_posix()
                    target = (
                        target_root / candidate.source_path
                        if target_is_folder(target_relative, project)
                        else target_root
                    )
                    if target.is_file() and not target.is_symlink():
                        installed = "yes"
                    click.echo(
                        f"{candidate.filename}\t{source.canonical_source}\t"
                        f"{candidate.source_path}\t{installed}"
                    )
    except (LocationParseError, DiscoveryError, OSError, TypeError, ValueError) as exc:
        raise _error(exc) from None


def _literal(value: str) -> str:
    """Normalize a CLI path selector to the plural guideline suffix."""
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
    """Add a normalized declaration and synchronize it immediately.

    .. versionchanged:: 0.2.0
        Exact path selectors and aliases are retained in the manifest and
        lock identity.

    Args:
        location:
            Source expression to add or replace.
        pattern:
            Optional suffix-stripped filename pattern.
        ref:
            Optional source revision.
        target_path:
            Optional project-relative destination.
        alias:
            Optional display name for the declaration.
        paths:
            Optional exact source-relative selectors.

    Examples:
        From a shell, add a source and select one repository-relative file:

        .. code-block:: console

            $ guidelines add https://example.com/team/repo --path python/testing.guidelines.md
    """
    project = _project()
    try:
        with atomic.advisory_lock(operation_lock_path(project)):
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
                parsed = with_source_path(parsed, path, kind="file")
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
                    if item.canonical_source_for(project)
                    == declaration.canonical_source_for(project)
                ),
                None,
            )
            if existing is None:
                document.guidelines.append(declaration)
            else:
                document.guidelines[document.guidelines.index(existing)] = declaration
            state_journal = ReconciliationJournal(project)
            state_journal.capture([project / "guidelines.yml", project / "guidelines.lock.json"])
            try:
                save_manifest(project / "guidelines.yml", document)
                sync_manifest(project, operation_lock_held=True)
                state_journal.commit()
            except Exception:
                state_journal.rollback()
                raise
            click.echo(
                f"Guidelines source {'added' if existing is None else 'updated'}: "
                f"{alias or parsed.display_name}"
            )
    except (LocationParseError, OSError, SyncError, TypeError, ValueError) as exc:
        raise _error(exc) from None


@guidelines.command("remove")
@click.argument("identifier")
def remove(identifier: str) -> None:
    """Remove a declaration while preserving installed files.

    Args:
        identifier:
            Alias, display name, or canonical source identity.
    """
    project = _project()
    path = project / "guidelines.yml"
    try:
        with atomic.advisory_lock(operation_lock_path(project)):
            if not path.exists():
                raise click.ClickException("No guidelines.yml manifest exists.")
            document = _manifest(project)
            try:
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
                    for item in document.guidelines
                    if item.alias == identifier
                    or item.display_name.casefold() == identifier.casefold()
                ]
            if len(matches) != 1:
                raise click.ClickException(
                    "No guideline source matched the identifier."
                    if not matches
                    else "Ambiguous guideline identifier."
                )
            removed = matches[0]
            document.guidelines.remove(removed)
            lock_path = project / "guidelines.lock.json"
            state_journal = ReconciliationJournal(project)
            state_journal.capture([path, lock_path])
            try:
                save_manifest(path, document)
                if lock_path.exists():
                    from ai_guidelines.lockfile import load_lockfile, save_lockfile

                    lock_document = load_lockfile(lock_path)
                    lock_document.guidelines = [
                        entry
                        for entry in lock_document.guidelines
                        if not entry.matches(removed, base_dir=project)
                    ]
                    save_lockfile(lock_path, lock_document)
                state_journal.commit()
            except Exception:
                state_journal.rollback()
                raise
            click.echo(f"Guidelines source removed: {matches[0].display_name}")
            click.echo("No guideline files were deleted or modified.")
    except (LocationParseError, OSError, SyncError, TypeError, ValueError) as exc:
        raise _error(exc) from None


@guidelines.command("outdated")
def outdated() -> None:
    """Report available revisions without changing project state.

    .. versionchanged:: 0.2.0
        Outdated inspection is read-only and reports provider-resolved revision
        status separately from the current lock revision.
    """
    try:
        print_outdated_report(inspect_outdated(_project()))
    except (FileNotFoundError, GuidelineUpdateError, SourceFetchError, OSError, ValueError) as exc:
        raise _error(exc) from None


@guidelines.command("update")
@click.option("--yes", is_flag=True)
@click.option("--interactive", "interactive", is_flag=True, default=False)
@click.option("--dry-run/--no-dry-run", default=False)
def update(
    *,
    yes: bool,
    interactive: bool,
    dry_run: bool,
) -> None:
    """Plan and apply updates to moving sources.

    .. versionchanged:: 0.2.0
        Application verifies the reviewed plan fingerprint and exact source
        resolution before writing files.

    Args:
        yes:
            Apply changes without an interactive confirmation prompt.
        interactive:
            Ask for confirmation before applying changes.
        dry_run:
            Print the plan without applying it.

    Examples:
        From a shell, preview moving-source updates without changing files:

        .. code-block:: console

            $ guidelines update --dry-run
    """
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
    """Maintain the disposable guideline cache.

    .. versionchanged:: 0.2.0
        Cache commands operate on credential-free commit-addressed snapshots.
    """


def _cache() -> MaterializedGuidelineCache:
    """Return the default materialized guideline cache service."""
    return MaterializedGuidelineCache()


@cache.command("clean")
def cache_clean() -> None:
    """Remove all disposable snapshots."""
    click.echo(f"Removed {_cache().clear_all()} cache entries.")


@cache.command("prune")
def cache_prune() -> None:
    """Remove expired disposable snapshots according to the eviction TTL."""
    click.echo(f"Pruned {_cache().cleanup()} expired cache entries.")


@cache.command("dir")
def cache_dir() -> None:
    """Print the disposable cache directory without creating it."""
    click.echo(_cache().cache_dir)


@cache.command("size")
def cache_size() -> None:
    """Print disposable cache size in bytes."""
    click.echo(f"Cache size: {_cache().size()} bytes")


guidelines.add_command(cache)
