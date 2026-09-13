"""Tests for the release-note output validator."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[3]
    / ".github"
    / "skills"
    / "gh-release-notes"
    / "scripts"
    / "generate_release_notes.py"
)


def run_validator(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    """Run validation through the same entry point used by CI."""
    notes_path = tmp_path / "release-notes.md"
    notes_path.write_text(content, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--validate", str(notes_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_accepts_maintenance_notes(tmp_path: Path) -> None:
    """Accept the deterministic empty-range output."""
    result = run_validator(
        tmp_path,
        "## Maintenance\n\n"
        "This release contains maintenance and internal improvements. "
        "No user-facing behavior changed.\n",
    )

    assert result.returncode == 0, result.stderr


def test_validate_rejects_missing_release_heading(tmp_path: Path) -> None:
    """Reject output that cannot be used as a GitHub Release body."""
    result = run_validator(tmp_path, "Generated title\n\nNo release section.")

    assert result.returncode == 1
    assert "permitted section headings" in result.stderr


def test_validate_rejects_trace_and_title_leakage(tmp_path: Path) -> None:
    """Keep Copilot traces and version headings out of published notes."""
    result = run_validator(tmp_path, "## Bug Fixes\n\n# v0.2.0\n\nto=bash.exec code\n")

    assert result.returncode == 1
    assert "title heading" in result.stderr or "trace markers" in result.stderr


@pytest.mark.parametrize("content", ["", "\n", "   "])
def test_validate_rejects_empty_output(tmp_path: Path, content: str) -> None:
    """Reject empty handoff files."""
    result = run_validator(tmp_path, content)

    assert result.returncode == 1
    assert "empty" in result.stderr.lower()
