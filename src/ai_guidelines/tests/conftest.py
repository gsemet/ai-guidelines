"""Shared pytest fixtures for the ai-guidelines test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


def _find_project_root(start: Path) -> Path:
    """Walk upward until the directory containing ``pyproject.toml`` is found.

    Args:
        start: Directory to start searching from.

    Returns:
        The repository root.

    Raises:
        RuntimeError: If no ``pyproject.toml`` ancestor exists.
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"no pyproject.toml found above {start}")


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the repository root, discovered rather than computed by index.

    Contract tests read repository files such as ``README.md``. Deriving the
    root from a fixed ``parents[N]`` index breaks whenever the test tree moves;
    discovery by marker file does not.
    """
    return _find_project_root(Path(__file__).resolve().parent)


@pytest.fixture(scope="session")
def vectors_dir() -> Path:
    """Return the directory holding test data vectors for the contract suite."""
    return Path(__file__).resolve().parent / "vectors"
