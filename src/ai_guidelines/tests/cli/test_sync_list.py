"""CLI contract tests for synchronization and target inspection."""

from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cli import guidelines


def _write(path: Path, content: str = "guide\n") -> None:
    """Write a UTF-8 CLI fixture and create its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    """Create a project with one local guideline declaration."""
    project = tmp_path / "project"
    project.mkdir()
    _write(project / "source" / "team.guidelines.md")
    (project / "guidelines.yml").write_text(
        "version: 1\nguidelines:\n- source: ./source/\n", encoding="utf-8"
    )
    return project


def test_sync_dry_run_is_deterministic_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    """Dry-run output describes changes without creating targets or locks."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    result = runner.invoke(guidelines, ["sync", "--dry-run"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Dry run: no files were written." in result.output
    assert not (project / ".github").exists()
    assert not (project / "guidelines.lock.json").exists()


def test_sync_redirected_output_contains_no_terminal_control_sequences(
    tmp_path: Path, monkeypatch
) -> None:
    """Reports remain plain text when captured by a caller."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(guidelines, ["sync", "--dry-run"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "\x1b[" not in result.output
    assert "Guideline synchronization" in result.output


def test_sync_frozen_without_lock_fails_without_writes(tmp_path: Path, monkeypatch) -> None:
    """Frozen mode rejects unavailable replay state before mutation."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(guidelines, ["sync", "--frozen"])

    assert result.exit_code != 0
    assert "Frozen synchronization failed" in result.output
    assert not (project / ".github").exists()
    assert not (project / "guidelines.lock.json").exists()


def test_list_reports_source_backed_and_locally_edited_files(tmp_path: Path, monkeypatch) -> None:
    """List reports ownership and detects changes to managed targets."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    target = project / ".github/guidelines/team.guidelines.md"
    target.write_text("edited\n", encoding="utf-8")

    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "source-backed" in result.output
    assert "locally edited" in result.output


def test_list_does_not_follow_external_symlinks(tmp_path: Path, monkeypatch) -> None:
    """External symlinked guideline files are omitted from inspection."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    target = project / ".github/guidelines"
    target.mkdir(parents=True)
    external = tmp_path / "external.guidelines.md"
    external.write_text("external\n", encoding="utf-8")
    (target / external.name).symlink_to(external)

    result = CliRunner().invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "external.guidelines.md" not in result.output


def test_list_reports_all_declared_targets(tmp_path: Path, monkeypatch) -> None:
    """List inspects every declared target, not only the default target."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    source_two = tmp_path / "source-two"
    project.mkdir()
    source.mkdir()
    source_two.mkdir()
    _write(source / "one.guideline.md")
    _write(source / "two.guideline.md")
    _write(source_two / "two.guideline.md")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n  target_path: .first\n"
        f"- source: {source_two / 'two.guideline.md'}\n  target_path: .second\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert ".first/one.guidelines.md" in result.output
    assert ".first/two.guidelines.md" in result.output
    assert ".second/two.guidelines.md" in result.output


def test_sync_reports_ordinary_counts_and_sanitizes_source_errors(
    tmp_path: Path, monkeypatch
) -> None:
    """Normal synchronization reports file actions and hides raw fetch diagnostics."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(guidelines, ["sync"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Actions:" in result.output
    assert "added" in result.output

    (project / "guidelines.yml").write_text(
        "version: 1\nguidelines:\n- source: https://example.invalid/private.git\n",
        encoding="utf-8",
    )
    failed = CliRunner().invoke(guidelines, ["sync"])
    assert failed.exit_code != 0
    assert "Could not synchronize guideline source" in failed.output


def test_list_reports_manual_historical_altered_unreadable_and_empty_states(
    tmp_path: Path, monkeypatch
) -> None:
    """List distinguishes unmanaged, missing, modified, and empty target states."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    managed = project / ".github/guidelines/team.guidelines.md"
    managed.write_text("changed\n", encoding="utf-8")
    _write(project / ".github/guidelines/manual.guideline.md", "manual\n")
    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)
    assert "source-backed" in result.output
    assert "locally edited" in result.output
    assert "manual" in result.output

    managed.unlink()
    missing = runner.invoke(guidelines, ["list"], catch_exceptions=False)
    assert "unreadable" in missing.output

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert runner.invoke(guidelines, ["list"], catch_exceptions=False).exit_code == 0
    assert "No guideline files found" in runner.invoke(guidelines, ["list"]).output


def test_list_reports_declared_empty_target_and_historical_record(
    tmp_path: Path, monkeypatch
) -> None:
    """List retains target visibility when a previously managed file is missing."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    _write(source / "guide.guideline.md")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n  target_path: .historical\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    (project / ".historical/guide.guidelines.md").unlink()

    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Guidelines in .historical" in result.output
    assert "guide.guidelines.md\tsource-backed\tunreadable" in result.output


def test_list_does_not_follow_unreadable_target_symlink(tmp_path: Path, monkeypatch) -> None:
    """An external target symlink is reported without exposing its files."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    _write(outside / "secret.guideline.md", "secret\n")
    (project / "guidelines.yml").write_text(
        "version: 1\nguidelines:\n- source: ./source\n  target_path: .target\n",
        encoding="utf-8",
    )
    (project / ".target").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "secret.guideline.md" not in result.output
    assert "Target is unreadable." in result.output


def test_list_retains_historical_target_after_declaration_is_removed(
    tmp_path: Path, monkeypatch
) -> None:
    """A lock-only target is visible as historical, not silently dropped."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    (project / "guidelines.yml").write_text("version: 1\nguidelines: []\n", encoding="utf-8")
    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Guidelines in .github/guidelines" in result.output
    assert "historical" in result.output


def test_list_classifies_altered_historical_file(tmp_path: Path, monkeypatch) -> None:
    """A changed file from a removed declaration remains historical and locally edited."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    target = project / ".github/guidelines/team.guidelines.md"
    (project / "guidelines.yml").write_text("version: 1\nguidelines: []\n", encoding="utf-8")
    target.write_text("historically changed\n", encoding="utf-8")

    result = runner.invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "historical" in result.output
    assert "locally edited" in result.output


def test_list_keeps_source_backed_classification_when_display_names_overlap(
    tmp_path: Path, monkeypatch
) -> None:
    """Ownership follows the active declaration, not a historical lock name."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    result = runner.invoke(guidelines, ["list"])
    assert result.exit_code == 0
    assert "team.guidelines.md\tsource-backed" in result.output


def test_list_reports_permission_denied_target(tmp_path: Path, monkeypatch) -> None:
    """Permission-denied target inspection is reported as unreadable."""
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    target = project / ".github/guidelines"
    target.mkdir(parents=True)
    from ai_guidelines import cli

    monkeypatch.setattr(cli.os, "access", lambda _path, _mode: False)
    result = CliRunner().invoke(guidelines, ["list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Target is unreadable." in result.output
