"""CLI contract tests for guideline discovery."""

from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cli import guidelines


def test_search_location_first_matches_suffix_stripped_name(tmp_path: Path) -> None:
    """A positional location may be followed by a filename glob."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "Team.guidelines.md").write_text("team\n", encoding="utf-8")
    (source / "other.guideline.md").write_text("other\n", encoding="utf-8")

    result = CliRunner().invoke(guidelines, ["search", str(source), "Team"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "Team.guidelines.md" in result.output
    assert "other.guideline.md" not in result.output


def test_search_query_cannot_be_combined_with_location(tmp_path: Path) -> None:
    """Configured query syntax is reserved for manifest-wide searches."""
    result = CliRunner().invoke(
        guidelines, ["search", str(tmp_path), "--query", "team"], catch_exceptions=False
    )

    assert result.exit_code != 0
    assert "--query cannot be used with a location" in result.output


def test_search_no_cache_does_not_leave_discovery_cache_in_project(tmp_path: Path) -> None:
    """No-cache discovery remains disposable and project-local state is unchanged."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "team.guidelines.md").write_text("team\n", encoding="utf-8")

    result = CliRunner().invoke(guidelines, ["search", str(source), "--no-cache"])

    assert result.exit_code == 0
    assert "team.guidelines.md" in result.output
    assert not (tmp_path / "guidelines.yml").exists()
    assert not (tmp_path / "guidelines.lock.json").exists()


def test_search_uses_each_declaration_target_for_installed_status(
    tmp_path: Path, monkeypatch
) -> None:
    """Configured search resolves installed status against its declaration target."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    (source / "team.guidelines.md").write_text("team\n", encoding="utf-8")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n  target_path: .custom\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    result = runner.invoke(guidelines, ["search", "--query", "team"], catch_exceptions=False)
    assert result.exit_code == 0
    assert result.output.rstrip().endswith("yes")


def test_search_is_case_sensitive_and_refreshes_cached_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    """Search matches exact-case stems and refresh can observe source changes."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "Team.guidelines.md").write_text("v1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["search", str(source), "team"]).output == ""
    assert "Team.guidelines.md" in runner.invoke(guidelines, ["search", str(source), "Team"]).output
    (source / "New.guideline.md").write_text("new\n", encoding="utf-8")
    assert "New.guideline.md" not in runner.invoke(guidelines, ["search", str(source)]).output
    assert (
        "New.guideline.md"
        in runner.invoke(guidelines, ["search", str(source), "New", "--refresh"]).output
    )


def test_search_configured_query_filters_each_declaration(tmp_path: Path, monkeypatch) -> None:
    """Configured searches apply the same filename-stem filter to each source."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    for name in ("one.guideline.md", "two.guidelines.md"):
        (source / name).write_text(name, encoding="utf-8")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    result = CliRunner().invoke(guidelines, ["search", "--query", "two"])
    assert result.exit_code == 0
    assert "two.guidelines.md" in result.output
    assert "one.guideline.md" not in result.output
