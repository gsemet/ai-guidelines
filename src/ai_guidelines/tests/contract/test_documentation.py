"""Contract tests for the public documentation and examples."""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import yaml

REQUIRED_FILES = (
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "examples/commit-message.guideline.md",
    "examples/python-testing.guideline.md",
    "examples/ui-design-system.guideline.md",
    "examples/project-specific.guideline.md",
    "examples/project-loading-instruction.md",
)
REQUIRED_DOC_PAGES = (
    "docs/source/conf.py",
    "docs/source/index.md",
    "docs/source/installation.md",
    "docs/source/tutorials/index.md",
    "docs/source/tutorials/getting-started.md",
    "docs/source/how-to/index.md",
    "docs/source/reference/index.md",
    "docs/source/reference/cli.md",
    "docs/source/reference/api.md",
    "docs/source/reference/manifest.md",
    "docs/source/reference/lockfile.md",
    "docs/source/reference/source-grammar.md",
    "docs/source/explanation/index.md",
    "docs/source/explanation/why-guidelines.md",
    "docs/source/explanation/caching.md",
    "docs/source/explanation/apm-comparison.md",
)
EXAMPLE_SUFFIXES = (".guideline.md", ".guidelines.md")


def test_required_documents_and_local_links_exist(project_root: Path) -> None:
    """Every published document exists and relative Markdown links resolve.

    Link resolution inside ``docs/source`` is enforced by ``sphinx-build -W``;
    this test covers the repository-root Markdown that Sphinx does not read.
    """
    for relative in REQUIRED_FILES:
        assert (project_root / relative).is_file(), relative

    markdown_files = [project_root / relative for relative in REQUIRED_FILES]
    link_pattern = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    for document in markdown_files:
        for target in link_pattern.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / target).resolve()
            assert resolved.is_file() or resolved.is_dir(), (document, target)


def test_diataxis_documentation_quadrants_are_populated(project_root: Path) -> None:
    """The Sphinx tree provides all four Diataxis quadrants."""
    for relative in REQUIRED_DOC_PAGES:
        path = project_root / relative
        assert path.is_file(), relative
        assert path.read_text(encoding="utf-8").strip(), relative


def test_examples_have_supported_suffixes_and_permissive_frontmatter(project_root: Path) -> None:
    """Examples are guideline files with optional valid YAML frontmatter."""
    examples = sorted(
        path
        for path in (project_root / "examples").glob("*")
        if path.name.endswith(EXAMPLE_SUFFIXES)
    )
    assert {path.name for path in examples} >= {
        "commit-message.guideline.md",
        "python-testing.guideline.md",
        "ui-design-system.guideline.md",
        "project-specific.guideline.md",
    }
    for example in examples:
        assert example.name.endswith(EXAMPLE_SUFFIXES)
        document = frontmatter.load(example)
        assert document.content.strip()
        assert all(isinstance(key, str) for key in document.metadata)


def test_readme_quick_start_manifest_is_valid_yaml(project_root: Path) -> None:
    """The documented quick-start manifest can be parsed by the YAML loader."""
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    match = re.search(r"```yaml\n(?P<manifest>.*?)\n```", readme, re.DOTALL)
    assert match is not None

    manifest = yaml.safe_load(match.group("manifest"))
    assert manifest["version"] == 1
    assert manifest["guidelines"][0]["source"]


def test_documentation_states_public_boundaries_and_safety_contracts(project_root: Path) -> None:
    """Core conceptual, safety, consistency, and compatibility claims remain visible."""
    text = "\n".join(
        (project_root / relative).read_text(encoding="utf-8")
        for relative in (*REQUIRED_FILES, *(p for p in REQUIRED_DOC_PAGES if p.endswith(".md")))
    ).lower()
    for statement in (
        "progressive disclosure",
        "does not inject",
        "does not activate",
        "project-owned",
        "sha-256",
        "local edit",
        "never",
        "atomic",
        "best-effort",
        "lock publication",
        "trust",
        "no migration command",
        "apm",
    ):
        assert statement in text, statement


def test_published_documentation_has_no_internal_product_or_infrastructure_terms(
    project_root: Path,
) -> None:
    """Community documentation does not leak private product terminology."""
    text = "\n".join(
        (project_root / relative).read_text(encoding="utf-8")
        for relative in (*REQUIRED_FILES, *(p for p in REQUIRED_DOC_PAGES if p.endswith(".md")))
    ).lower()
    forbidden = (
        "artifactory",
        "gitlab ci",
        "renault",
        "ampere",
        "compendium",
        "copilot plugin",
        "internal host",
        "proxy variables",
    )
    for term in forbidden:
        assert term not in text, term
