"""Contract tests for the public GitHub Actions quality matrix."""

from pathlib import Path

import yaml


def test_workflow_has_public_cross_platform_python_matrix(project_root: Path) -> None:
    """CI covers the supported floor on each public runner family."""
    workflow = yaml.safe_load(
        (project_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["quality"]
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert "3.10" in matrix["python-version"]
    actions = [step["uses"] for step in job["steps"] if "uses" in step]
    assert "actions/checkout@v7.0.1" in actions
    assert "astral-sh/setup-uv@v10.0.1" in actions
    setup_uv = next(
        step for step in job["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup_uv["with"]["python-version"] == "${{ matrix.python-version }}"
    assert job["env"]["UV_PYTHON"] == "${{ matrix.python-version }}"


def test_workflow_runs_the_complete_gate_and_artifact_smoke(project_root: Path) -> None:
    """CI delegates to the local gate alias and validates an installed wheel.

    CI must not re-list the individual checks: duplicating them is exactly how
    CI and local runs drift apart. ``just ci-check`` is a one-line alias to
    ``preflight``, so invoking it is sufficient and provably equivalent.
    """
    workflow = yaml.safe_load(
        (project_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["quality"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    for command in (
        "just ci-check",
        "uv build",
        '--python "${{ matrix.python-version }}"',
        "uv pip install",
        "--help",
        'add "$source_path" quality',
        '" sync',
    ):
        assert command in commands, command

    justfile = (project_root / "justfile").read_text(encoding="utf-8")
    assert "ci-check: preflight" in justfile


def test_release_and_publish_workflows_use_trusted_publishing(project_root: Path) -> None:
    """Publishing uses OIDC Trusted Publishing rather than a stored API token."""
    for name, environment, environment_url in (
        ("publish.yml", "pypi", "https://pypi.org/p/ai-guidelines"),
        ("test-pypi.yml", "testpypi", "https://test.pypi.org/p/ai-guidelines"),
    ):
        workflow = yaml.safe_load(
            (project_root / ".github/workflows" / name).read_text(encoding="utf-8")
        )
        publish = workflow["jobs"]["publish"]
        assert publish["environment"]["name"] == environment, name
        assert publish["environment"]["url"] == environment_url, name
        assert publish["permissions"]["id-token"] == "write", name
        uses = [step["uses"] for step in publish["steps"] if "uses" in step]
        assert "pypa/gh-action-pypi-publish@release/v1" in uses, name
        text = (project_root / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "PYPI_API_TOKEN" not in text, name


def test_release_workflow_can_create_the_initial_tag(project_root: Path) -> None:
    """A new project can publish its first release through the normal workflow."""
    text = (project_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "No existing tag found" not in text
    assert 'git tag "${{ steps.bump.outputs.tag }}"' in text


def test_release_workflow_generates_notes_from_the_local_tag(project_root: Path) -> None:
    """Release notes validate against the local tag before it is pushed."""
    text = (project_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    notes_position = text.index("Generate release notes with Copilot")
    tag_position = text.index('git tag "${{ steps.bump.outputs.tag }}"')
    assert tag_position < notes_position
    assert "generate_release_notes.py" in text
    assert '--from-ref "$FROM_REF"' in text
    assert '--to-ref "$TO_REF"' in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    assert "copilot-requests: write" in text
    assert "actions/upload-artifact@v4" in text


def test_release_workflow_publishes_non_draft_releases(project_root: Path) -> None:
    """Publish the exact released tag through OIDC after a non-draft release."""
    workflow = yaml.safe_load(
        (project_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    )
    publish = workflow["jobs"]["publish"]

    assert publish["if"] == "${{ !inputs.draft }}"
    assert publish["needs"] == "release"
    assert publish["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/ai-guidelines",
    }
    assert publish["permissions"]["id-token"] == "write"
    assert any(
        step.get("with", {}).get("ref") == "${{ needs.release.outputs.tag }}"
        for step in publish["steps"]
    )
    assert any(
        "uvx --from twine twine check dist/*" in step.get("run", "") for step in publish["steps"]
    )
    assert any(
        step.get("uses") == "pypa/gh-action-pypi-publish@release/v1" for step in publish["steps"]
    )


def test_production_publish_workflow_requires_a_release_tag(project_root: Path) -> None:
    """Prevent production PyPI publication from a default-branch dispatch."""
    text = (project_root / ".github/workflows/publish.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "refs/tags/" in text
    assert "inputs.tag" not in text
    assert "github.ref_name" in text
    assert "repository-url:" not in text


def test_test_pypi_workflow_is_explicitly_non_production(project_root: Path) -> None:
    """Provide a separate TestPyPI path for release validation."""
    workflow_path = project_root / ".github/workflows/test-pypi.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    text = workflow_path.read_text(encoding="utf-8")

    assert workflow["jobs"]["publish"]["environment"]["name"] == "testpypi"
    assert "https://test.pypi.org/legacy/" in text
    assert "inputs.tag" in text
