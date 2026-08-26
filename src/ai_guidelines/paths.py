"""Resolve project-owned guideline targets without crossing path boundaries.

Target selection is deliberately separate from synchronization.  Callers can
choose an explicit declaration target, retain a lockfile target, use the
manifest default, or fall back to the project's existing layout.  Every
result is checked as a project-relative path, including existing symlink
components.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import GuidelineDeclaration, GuidelinesLockEntry


class TargetPathError(ValueError):
    """Raised when a guideline target is unsafe or leaves the project."""


def _project_root(project_root: Path | str) -> Path:
    """Resolve a project root without requiring it to exist yet."""
    try:
        return Path(project_root).expanduser().resolve()
    except OSError:
        raise TargetPathError("project root could not be resolved safely") from None


def _relative_target(value: str) -> str:
    """Normalize and validate one project-relative target path."""
    normalized = value.replace("\\", "/")
    if not normalized or "\x00" in normalized:
        raise TargetPathError("guideline target must be a non-empty relative path")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise TargetPathError("guideline target must contain printable characters")
    windows_path = PureWindowsPath(normalized)
    if (
        normalized.startswith("/")
        or PurePosixPath(normalized).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise TargetPathError("guideline target must be relative to the project")
    if ".." in PurePosixPath(normalized).parts:
        raise TargetPathError("guideline target must remain within the project")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        raise TargetPathError("guideline target must be a non-empty relative path")
    return "/".join(parts)


def _assert_contained(project_root: Path, candidate: Path) -> None:
    """Reject an existing target symlink chain that leaves the project."""
    try:
        if not candidate.resolve(strict=False).is_relative_to(project_root):
            raise TargetPathError("guideline target escapes the project root")
    except OSError:
        raise TargetPathError("guideline target could not be resolved safely") from None


def resolve_target_path(project_root: Path | str, target_path: str | Path) -> Path:
    """Resolve and validate a configured project-relative target.

    Args:
        project_root:
            Project root used as the containment boundary.
        target_path:
            Relative folder or explicit target filename.

    Returns:
        The configured path below ``project_root`` without resolving its final
        path components.

    Raises:
        TargetPathError:
            If the path is absolute, traversing, or escapes through a symlink.
    """
    root = _project_root(project_root)
    candidate = root / Path(_relative_target(str(target_path)))
    _assert_contained(root, candidate)
    return candidate


def resolve_guideline_target(project_root: Path | str) -> Path:
    """Select the default target from the project's existing layout.

    Args:
        project_root:
            Project directory containing an optional ``.agents`` layout.

    Returns:
        ``.agents/guidelines`` when that directory exists, otherwise
        ``.github/guidelines``.  The selected directory is not created.

    Raises:
        TargetPathError:
            If the selected target cannot be safely contained.
    """
    root = _project_root(project_root)
    relative = (
        ".agents/guidelines" if (root / ".agents" / "guidelines").is_dir() else ".github/guidelines"
    )
    return resolve_target_path(root, relative)


def select_guideline_target(
    project_root: Path | str,
    *,
    declaration: GuidelineDeclaration | None = None,
    lock_entry: GuidelinesLockEntry | None = None,
    default_target_path: str | None = None,
) -> Path:
    """Select a target using declaration, lock, default, and layout precedence.

    Args:
        project_root:
            Consumer project root.
        declaration:
            Optional declaration whose explicit target has highest precedence.
        lock_entry:
            Optional matching lock entry whose target is the second choice.
        default_target_path:
            Optional manifest-wide target fallback.

    Returns:
        A contained target path.  The directory is not created.
    """
    configured: str | None = None
    if declaration is not None:
        configured = declaration.normalized_target_path
    if configured is None and lock_entry is not None:
        configured = lock_entry.normalized_target_path
    if configured is None:
        configured = default_target_path
    return (
        resolve_guideline_target(project_root)
        if configured is None
        else resolve_target_path(project_root, configured)
    )


def pin_target_path(
    declaration: GuidelineDeclaration,
    project_root: Path | str,
    default_target_path: str | None = None,
) -> GuidelineDeclaration:
    """Return a declaration with its effective target persisted.

    Args:
        declaration:
            Manifest declaration to copy and pin.
        project_root:
            Consumer project root used for containment checks.
        default_target_path:
            Optional manifest-wide target fallback.

    Returns:
        A declaration whose target is normalized relative to ``project_root``.

    Raises:
        TypeError:
            If ``declaration`` is not a :class:`GuidelineDeclaration`.
        TargetPathError:
            If the effective target is unsafe.
    """
    from .models import GuidelineDeclaration

    if not isinstance(declaration, GuidelineDeclaration):
        raise TypeError("declaration must be a GuidelineDeclaration")
    target = select_guideline_target(
        project_root,
        declaration=declaration,
        default_target_path=default_target_path,
    )
    root = _project_root(project_root)
    return declaration.model_copy(update={"target_path": target.relative_to(root).as_posix()})


def target_is_folder(target_path: Path | str, project_root: Path | str | None = None) -> bool:
    """Return whether a target denotes a folder rather than one explicit file.

    Args:
        target_path:
            Configured relative target path.
        project_root:
            Optional project root used to inspect an existing target.

    Returns:
        ``True`` for trailing separators, existing directories, or extensionless
        paths.  Named files are treated as explicit targets.

    Raises:
        TargetPathError:
            If a supplied project root exposes an unsafe path.
    """
    value = str(target_path)
    if value.endswith(("/", "\\")):
        return True
    if project_root is not None:
        candidate = resolve_target_path(project_root, value)
        if candidate.exists():
            return candidate.is_dir()
    return Path(value).suffix == ""


def operation_lock_path(project_root: Path | str) -> Path:
    """Return the neutral project operation-lock path.

    The lock intentionally lives under ``.guidelines`` rather than any
    host-specific state directory.  The parent is created by the lock
    primitive only when a non-dry operation starts.  Like every other managed
    path, the parent symlink chain must remain inside the project.
    """
    return resolve_target_path(project_root, ".guidelines/operation.lock")


resolve_guidelines_target = resolve_guideline_target
pin_guideline_target = pin_target_path
