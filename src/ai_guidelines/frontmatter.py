"""Permissive YAML frontmatter readers for guideline Markdown files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter


def read_guideline_frontmatter(path: Path) -> dict[str, Any]:
    """Read optional YAML frontmatter from one guideline file.

    A Markdown file without a frontmatter block is valid and returns an empty
    mapping. Parsing errors remain visible to callers so discovery can decide
    whether to warn and continue or fail a higher-level operation.
    """
    post = frontmatter.load(str(path))
    return dict(post.metadata)


def read_guideline_meta(path: Path) -> dict[str, Any]:
    """Alias for :func:`read_guideline_frontmatter`."""
    return read_guideline_frontmatter(path)


read_frontmatter = read_guideline_frontmatter
