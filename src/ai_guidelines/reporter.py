"""Deterministic terminal reports for the standalone guidelines CLI."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from ai_guidelines.cache import MaterializedGuidelineCache


def _console() -> Console:
    return Console(force_terminal=False, color_system=None)


def print_sync_report(result: Any) -> None:
    console = _console()
    console.print("Guideline synchronization")
    console.print(f"Target: {result.plan.target_path}")
    console.print(f"Lockfile: {result.plan.lock_path}")
    for entry in result.plan.entries:
        counts = (
            ", ".join(
                f"{name}={len(entry.actions.get(name, []))}"
                for name in ("added", "updated", "removed", "unchanged")
                if entry.actions.get(name)
            )
            or "no changes"
        )
        console.print(f"{entry.name}: {entry.action}; {counts}")
    counts = result.plan.action_counts
    console.print(
        f"Actions: {counts['added']} added, {counts['updated']} updated, "
        f"{counts['removed']} removed, {counts['unchanged']} unchanged"
    )
    for warning in result.warnings:
        console.print(f"Warning: {warning}")
    if result.dry_run:
        console.print("Dry run: no files were written.")
    else:
        console.print("Synchronization complete.")


def print_outdated_report(report: Any) -> None:
    console = _console()
    table = Table(title="Guideline revisions")
    for column in ("Name", "Current", "Available", "Status"):
        table.add_column(column)
    for entry in report.entries:
        table.add_row(entry.name, entry.current_revision, entry.available_revision, entry.status)
    console.print(table)
    console.print(
        "No guideline updates are available."
        if not report.updates_available
        else f"{len(report.updates_available)} update(s) available."
    )


def print_update_plan(plan: Any) -> None:
    console = _console()
    console.print("Guideline update plan")
    console.print(f"Cache base: {MaterializedGuidelineCache().cache_dir}")
    for entry in plan.entries:
        changed = sum(len(entry.actions.get(name, [])) for name in ("added", "updated", "removed"))
        console.print(
            f"{entry.name}: {entry.group}; {entry.current_revision} -> "
            f"{entry.available_revision}; {changed} file(s)"
        )
    if plan.dry_run:
        console.print("Dry run: no files were written.")


def print_update_result(result: Any) -> None:
    changed = sum(
        len(item.added) + len(item.updated) + len(item.removed) for item in result.reconciliations
    )
    _console().print(f"Update complete: {changed} file(s) changed.")
