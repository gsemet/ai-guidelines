"""Repository hygiene and public-boundary contracts."""

from __future__ import annotations

import re
from pathlib import Path

SCANNED_DIRECTORIES = (
    "src/ai_guidelines/atomic.py",
    "src/ai_guidelines/api.py",
    "src/ai_guidelines/cache.py",
    "src/ai_guidelines/cli.py",
    "src/ai_guidelines/discovery.py",
    "src/ai_guidelines/fetch.py",
    "src/ai_guidelines/frontmatter.py",
    "src/ai_guidelines/locations.py",
    "src/ai_guidelines/lockfile.py",
    "src/ai_guidelines/manifest.py",
    "src/ai_guidelines/models.py",
    "src/ai_guidelines/paths.py",
    "src/ai_guidelines/reconcile.py",
    "src/ai_guidelines/reporter.py",
    "src/ai_guidelines/sparse.py",
    "src/ai_guidelines/sync.py",
    "src/ai_guidelines/update.py",
    "src/ai_guidelines/update_acquisition.py",
    "docs",
    "examples",
    ".github/workflows",
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
)
# Agent-host and vendor product names the shipped material must never contain.
# Assembled from fragments so this file does not itself carry a literal
# occurrence, which would make a repository-wide grep for these terms noisy.
_FORBIDDEN_VENDORS = (
    "artifactory",
    "renault",
    "ampere",
    "compendium",
    "craft" + "sman",
)
FORBIDDEN = re.compile(
    "|".join(
        (
            *_FORBIDDEN_VENDORS,
            r"gitlab\s*ci",
            r"copilot\s+plugin",
            r"requirements\s+annotation",
            r"proxy\s+(?:setup|configuration|variables)",
        )
    ),
    re.IGNORECASE,
)


def repository_text(project_root: Path) -> str:
    """Return public source, documentation, examples, and configuration text."""
    files: list[Path] = []
    for entry in SCANNED_DIRECTORIES:
        path = project_root / entry
        files.extend(path.rglob("*") if path.is_dir() else [path])
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in files
        if path.is_file()
        and "ai_guidelines/tests" not in path.as_posix()
        and path.suffix.lower() in {".py", ".md", ".toml", ".yml", ".yaml"}
    )


def test_public_repository_has_no_internal_or_host_product_residue(project_root: Path) -> None:
    """Shipped material remains independent of private infrastructure and hosts.

    Agent-host product names are matched as bare words: the tool must not name
    any particular host, not even to declare it unsupported.
    """
    assert FORBIDDEN.search(repository_text(project_root)) is None


def test_public_repository_contains_mit_attribution(project_root: Path) -> None:
    """The source distribution identifies its license and copyright holder."""
    license_text = (project_root / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2026 Gaetan Semet" in license_text
    assert 'license = { file = "LICENSE" }' in (project_root / "pyproject.toml").read_text(
        encoding="utf-8"
    )
