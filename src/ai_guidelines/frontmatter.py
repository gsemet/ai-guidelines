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

    Args:
        path:
            Markdown file to inspect.

    Returns:
        Parsed YAML metadata, or an empty mapping when no block is present.

    Raises:
        OSError:
            If the file cannot be read.
        yaml.YAMLError:
            If the frontmatter is malformed.
    """
    post = frontmatter.load(str(path))
    return dict(post.metadata)


def read_guideline_meta(path: Path) -> dict[str, Any]:
    """Return the same metadata as :func:`read_guideline_frontmatter`.

    Args:
        path:
            Markdown file to inspect.

    Returns:
        Parsed YAML metadata for the guideline.
    """
    return read_guideline_frontmatter(path)


read_frontmatter = read_guideline_frontmatter
