"""CLI contract tests for update behavior."""

from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cli import guidelines
from ai_guidelines.update import UpdatePlan, UpdatePlanEntry


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project and moving local source for update tests."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    (source / "guide.guidelines.md").write_text("v1\n", encoding="utf-8")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n  ref: main\n", encoding="utf-8"
    )
    return project, source


def test_update_dry_run_and_interactive_decline_are_write_free(tmp_path: Path, monkeypatch) -> None:
    """Preview and declined interactive updates do not publish state."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    dry_run = runner.invoke(guidelines, ["update", "--dry-run"])
    declined = runner.invoke(guidelines, ["update", "--interactive"], input="n\n")

    assert dry_run.exit_code == 0
    assert declined.exit_code == 0
    assert "Dry run: no files were written." in dry_run.output
    assert "No changes applied." in declined.output or "No guideline updates" in declined.output
    assert not (project / "guidelines.lock.json").exists()
    assert not (project / ".github").exists()


def test_update_default_applies_and_reports_no_change_on_replay(
    tmp_path: Path, monkeypatch
) -> None:
    """Default update applies a source and subsequently reports no changes."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    first = runner.invoke(guidelines, ["update"])
    second = runner.invoke(guidelines, ["update"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "Update complete" in first.output
    assert "No guideline updates are available." in second.output
    assert (project / "guidelines.lock.json").exists()


def test_update_reports_clone_cache_base_path(tmp_path: Path, monkeypatch) -> None:
    """Update output identifies the base directory used for remote clones."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(guidelines, ["update", "--dry-run"])

    assert result.exit_code == 0
    assert "Cache base:" in result.output


def test_update_interactive_approval_applies_reviewed_plan(tmp_path: Path, monkeypatch) -> None:
    """An explicit approval applies the immutable plan produced by inspection."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    result = CliRunner().invoke(guidelines, ["update", "--interactive"], input="y\n")

    assert result.exit_code == 0
    assert "Update complete" in result.output
    assert (project / "guidelines.lock.json").exists()


def test_outdated_reports_pinned_and_moving_revisions(tmp_path: Path, monkeypatch) -> None:
    """Outdated keeps exact revisions pinned while reporting moving sources."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    report = runner.invoke(guidelines, ["outdated"])
    assert report.exit_code == 0
    assert "Guideline revisions" in report.output


def test_update_yes_and_frozen_rejects_changed_local_revision(tmp_path: Path, monkeypatch) -> None:
    """Explicit approval applies a plan and frozen mode rejects altered local state."""
    project, source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    first = runner.invoke(guidelines, ["update", "--yes"])
    assert first.exit_code == 0
    original = (project / ".github/guidelines/guide.guidelines.md").read_text()
    (source / "guide.guidelines.md").write_text("v2\n", encoding="utf-8")
    replay = runner.invoke(guidelines, ["sync", "--frozen"])
    assert replay.exit_code != 0
    assert "Frozen synchronization failed" in replay.output
    assert (project / ".github/guidelines/guide.guidelines.md").read_text() == original
    applied = runner.invoke(guidelines, ["update"])
    assert applied.exit_code == 0
    assert "Update complete" in applied.output
    assert (project / ".github/guidelines/guide.guidelines.md").read_text() == "v2\n"


def test_pinned_update_reports_no_change_without_creating_lock(tmp_path: Path, monkeypatch) -> None:
    """Pinned declarations do not resolve or create update state."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    from ai_guidelines import cli

    monkeypatch.setattr(
        cli,
        "build_update_plan",
        lambda _project: UpdatePlan(
            entries=[
                UpdatePlanEntry(
                    name="guide",
                    source="local/source",
                    status="pinned",
                    target_path=".github/guidelines",
                )
            ]
        ),
    )

    result = CliRunner().invoke(guidelines, ["update"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "unchanged" in result.output
    assert "No guideline updates are available." in result.output
    assert not (project / "guidelines.lock.json").exists()


def test_update_replays_the_reviewed_revision_and_preserves_plan_state(
    tmp_path: Path, monkeypatch
) -> None:
    """The CLI passes a reviewed plan to the application without changing it."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    reviewed = UpdatePlan(
        entries=[
            UpdatePlanEntry(
                name="guide",
                source="https://example.com/team/repo",
                status="updated",
                target_path=".github/guidelines",
                commit="a" * 40,
            )
        ]
    )
    received: list[UpdatePlan] = []
    from ai_guidelines import cli
    from ai_guidelines.models import GuidelinesLock
    from ai_guidelines.update import GuidelineUpdateResult

    monkeypatch.setattr(cli, "build_update_plan", lambda _project: reviewed)
    monkeypatch.setattr(
        cli,
        "apply_update_plan",
        lambda _project, plan: (
            received.append(plan)
            or GuidelineUpdateResult(plan=plan, lockfile=GuidelinesLock(), applied=True)
        ),
    )
    result = runner.invoke(guidelines, ["update", "--yes"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Update complete" in result.output
    assert received == [reviewed.model_copy(update={"dry_run": False})]


def test_update_rejects_resolution_changed_after_review(tmp_path: Path, monkeypatch) -> None:
    """Application refuses a plan whose reviewed resolution no longer matches acquisition."""
    project, _source = _project(tmp_path)
    monkeypatch.chdir(project)
    reviewed = UpdatePlan(
        entries=[
            UpdatePlanEntry(
                name="guide",
                source="https://example.com/team/repo",
                current_revision="old",
                available_revision="new",
                resolved_ref="reviewed",
                status="updated",
                target_path=".github/guidelines",
            )
        ]
    )
    from ai_guidelines import cli
    from ai_guidelines.update import GuidelineUpdateError

    monkeypatch.setattr(cli, "build_update_plan", lambda _project: reviewed)
    monkeypatch.setattr(
        cli,
        "apply_update_plan",
        lambda _project, _plan: (_ for _ in ()).throw(
            GuidelineUpdateError("changed after planning")
        ),
    )
    result = CliRunner().invoke(guidelines, ["update", "--yes"])

    assert result.exit_code != 0
    assert "changed after planning" in result.output


def test_update_applies_changed_local_source_and_replays_it(tmp_path: Path, monkeypatch) -> None:
    """Moving local sources produce an update and the new lock replays it."""
    project, source = _project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    (source / "guide.guidelines.md").write_text("v2\n", encoding="utf-8")

    result = runner.invoke(guidelines, ["update"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "updated" in result.output
    assert (project / ".github/guidelines/guide.guidelines.md").read_text() == "v2\n"
    replay = runner.invoke(guidelines, ["sync", "--frozen"])
    assert replay.exit_code == 0, replay.output
    assert "0 updated" in replay.output
