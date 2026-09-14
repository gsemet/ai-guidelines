"""Public contracts for reproducible project-owned AI guidelines."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version
from typing import Any, cast

from ai_guidelines.api import (
    DiscoveryResult,
    GuidelinesSyncResult,
    GuidelineUpdateResult,
    OutdatedReport,
    SourceLocation,
    UpdatePlan,
    apply_update_plan,
    build_update_plan,
    cache_clean,
    cache_dir,
    cache_prune,
    cache_size,
    discover_guidelines,
    inspect_outdated,
    load_lockfile,
    load_manifest,
    parse_location,
    save_lockfile,
    save_manifest,
    sync_manifest,
)
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
    GuidelinesManifest,
    SourceIdentity,
)

try:
    __version__ = _package_version("ai-guidelines")
except PackageNotFoundError:  # pragma: no cover - editable tree without metadata
    __version__ = "0.0.0.dev0"

__all__ = [
    "GuidelineDeclaration",
    "GuidelineFileRecord",
    "GuidelinesLock",
    "GuidelinesLockEntry",
    "GuidelinesManifest",
    "SourceIdentity",
    "__version__",
    "main",
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
    "DiscoveryResult",
    "GuidelineUpdateResult",
    "GuidelinesSyncResult",
    "OutdatedReport",
    "SourceLocation",
    "UpdatePlan",
]


from ai_guidelines.cli import guidelines


def main(*args: Any, **kwargs: Any) -> None:
    """Run the standalone command, accepting programmatic argument strings.

    Args:
        args:
            Positional command-line arguments passed to Click.
        kwargs:
            Keyword arguments passed to Click's command entry point.

    Examples:
        >>> from contextlib import redirect_stdout
        >>> from io import StringIO
        >>> output = StringIO()
        >>> with redirect_stdout(output):
        ...     main("cache", "dir", standalone_mode=False)
        >>> output.getvalue().strip().endswith("guideline-sources")
        True
    """
    guidelines.main(args=cast(list[str] | None, list(args) or None), **kwargs)
