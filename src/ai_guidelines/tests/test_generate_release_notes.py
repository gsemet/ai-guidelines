"""Tests for the release-note generation command."""

import runpy
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch

SCRIPT = (
    Path(__file__).parents[3]
    / ".github"
    / "skills"
    / "gh-release-notes"
    / "scripts"
    / "generate_release_notes.py"
)
MODULE: dict[str, Any] = runpy.run_path(str(SCRIPT))
build_copilot_command = MODULE["build_copilot_command"]
build_parser = MODULE["build_parser"]
build_prompt = MODULE["build_prompt"]
generate_release_notes = MODULE["generate_release_notes"]
require_copilot_token = MODULE["require_copilot_token"]
validate_output = MODULE["validate_output"]


def test_build_copilot_command_includes_ai_guidelines_documentation_access() -> None:
    """Allow the skill to inspect the package's published documentation."""
    command = build_copilot_command("release prompt", None)

    assert command[:3] == ["gh", "copilot", "--"]
    assert "--allow-url=https://ai-guidelines.readthedocs.io" in command
    assert "--allow-all-tools" in command
    assert "--available-tools=read,create,edit,bash" in command


def test_parser_accepts_generic_git_refs_and_maintenance_mode() -> None:
    """Support first-release root commits and deterministic maintenance notes."""
    args = build_parser().parse_args(
        ["--from-ref", "HEAD~1", "--to-ref", "HEAD", "--maintenance-only"]
    )

    assert args.from_ref == "HEAD~1"
    assert args.to_ref == "HEAD"
    assert args.maintenance_only is True


def test_build_prompt_requires_file_output_and_user_impact() -> None:
    """Keep orchestration in the script while the skill owns release-note policy."""
    prompt = build_prompt(
        "v0.1.0",
        "v0.2.0",
        Path("/tmp/project"),
        Path("/tmp/project/release-notes.md"),
        "Commit log\nabc123 feat: useful change",
    )

    assert "Use the /gh-release-notes skill" in prompt
    assert "v0.1.0..v0.2.0" in prompt
    assert "release-notes.md" in prompt
    assert "response stream is discarded" in prompt
    assert "<git-evidence>" in prompt
    assert "omit Maintenance" in prompt


def test_require_copilot_token_rejects_missing_authentication(monkeypatch: MonkeyPatch) -> None:
    """Fail before invoking Copilot when no supported token is available."""
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN"):
        require_copilot_token()


def test_require_copilot_token_accepts_actions_token(monkeypatch: MonkeyPatch) -> None:
    """Allow GitHub Actions authentication without a long-lived secret."""
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")

    require_copilot_token()


def test_generate_release_notes_writes_maintenance_notes_without_copilot(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Make empty or explicitly maintenance-only releases deterministic."""
    repo = tmp_path / "repo"
    repo.mkdir()
    globals_dict = generate_release_notes.__globals__
    monkeypatch.setitem(globals_dict, "validate_range", lambda *args: None)
    monkeypatch.setitem(globals_dict, "range_has_commits", lambda *args: False)
    monkeypatch.setitem(
        globals_dict,
        "run_copilot",
        lambda *args: pytest.fail("maintenance-only output must not invoke Copilot"),
    )

    generate_release_notes(repo, "HEAD~1", "HEAD", Path("release-notes.md"), None)

    assert (repo / "release-notes.md").read_text(encoding="utf-8") == (
        "## Maintenance\n\n"
        "This release contains maintenance and internal improvements. "
        "No user-facing behavior changed.\n"
    )


def test_validate_output_accepts_clean_release_notes(tmp_path: Path) -> None:
    """Preserve skill-authored Markdown after checking its output contract."""
    output = tmp_path / "release-notes.md"
    content = "## Bug Fixes\n\n- Fixed a user-visible issue.\n"
    output.write_text(content, encoding="utf-8")

    validate_output(output)

    assert output.read_text(encoding="utf-8") == content
