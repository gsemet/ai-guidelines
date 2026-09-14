"""Sphinx configuration for ai-guidelines."""

from __future__ import annotations

import importlib.metadata

project = "ai-guidelines"
author = "Gaetan Semet"
copyright = "2026, Gaetan Semet"

try:
    version = importlib.metadata.version("ai-guidelines")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover - unbuilt tree
    version = "0.0.0.dev0"
release = version

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx.ext.napoleon",
    "sphinx_click",
    "sphinx_autodoc_typehints",
    "sphinx_design",
    "sphinx_copybutton",
]

myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

exclude_patterns = ["_build"]

html_theme = "pydata_sphinx_theme"
html_title = f"ai-guidelines {version}"
html_static_path = ["_static"]
html_theme_options = {
    "github_url": "https://github.com/gsemet/ai-guidelines",
    "use_edit_page_button": True,
    "show_prev_next": True,
    "navigation_with_keys": False,
}
html_context = {
    "github_user": "gsemet",
    "github_repo": "ai-guidelines",
    "github_version": "main",
    "doc_path": "docs/source",
}

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

doctest_global_setup = """
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.discovery import discover_guidelines
from ai_guidelines.fetch import AcquiredSource
from ai_guidelines.locations import parse_location, with_source_path
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
    GuidelinesManifest,
    SourceIdentity,
)
from ai_guidelines.sync import sync_manifest
from ai_guidelines.update import apply_update_plan, build_update_plan, inspect_outdated
"""

# `always_use_bars_union` keeps rendered signatures consistent with the source.
always_use_bars_union = True
