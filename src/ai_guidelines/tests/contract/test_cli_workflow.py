"""Installed-style end-to-end workflow for the public CLI."""

from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cli import guidelines


@contextmanager
def _context(value):
    yield value


def test_local_source_workflow_through_public_command(tmp_path: Path, monkeypatch) -> None:
    """Declare, synchronize, inspect, search, update, remove, and clean cache."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    (source / "quality.guidelines.md").write_text("quality\n", encoding="utf-8")
    runner = CliRunner()

    def invoke(*arguments: str):
        return runner.invoke(guidelines, list(arguments), catch_exceptions=False)

    monkeypatch.chdir(project)
    assert invoke("add", str(source), "quality", "--alias", "quality").exit_code == 0
    assert invoke("sync").exit_code == 0
    assert invoke("list").exit_code == 0
    search = invoke("search", str(source), "quality")
    assert search.exit_code == 0
    assert "quality.guidelines.md" in search.output
    dry_run = invoke("update", "--dry-run")
    assert dry_run.exit_code == 0
    assert "Dry run: no files were written." in dry_run.output
    assert "quality.guidelines.md" in (project / "guidelines.lock.json").read_text(encoding="utf-8")
    before_manifest = (project / "guidelines.yml").read_bytes()
    before_lock = (project / "guidelines.lock.json").read_bytes()
    assert invoke("update", "--interactive").output.find("No guideline updates") >= 0
    assert (project / "guidelines.yml").read_bytes() == before_manifest
    assert (project / "guidelines.lock.json").read_bytes() == before_lock
    remove = invoke("remove", "quality")
    assert remove.exit_code == 0
    assert "quality.guidelines.md" not in (project / "guidelines.lock.json").read_text(
        encoding="utf-8"
    )
    assert (project / ".github/guidelines/quality.guidelines.md").exists()
    assert "guidelines: []" in (project / "guidelines.yml").read_text(encoding="utf-8")
    assert invoke("cache", "dir").exit_code == 0
    assert invoke("cache", "size").output.startswith("Cache size:")
    assert (project / "guidelines.yml").exists()
    assert (project / ".github/guidelines/quality.guidelines.md").exists()


def test_documented_lock_and_frozen_validation_workflow(tmp_path: Path, monkeypatch) -> None:
    """A lock produced by sync rejects changed local content without writes."""
    project = tmp_path / "project"
    source = tmp_path / "source"
    project.mkdir()
    source.mkdir()
    (source / "guide.guideline.md").write_text("one\n", encoding="utf-8")
    (project / "guidelines.yml").write_text(
        f"version: 1\nguidelines:\n- source: {source}\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["sync"]).exit_code == 0
    lock_before = (project / "guidelines.lock.json").read_bytes()
    target = project / ".github/guidelines/guide.guideline.md"
    source.joinpath("guide.guideline.md").write_text("two\n", encoding="utf-8")
    result = runner.invoke(guidelines, ["sync", "--frozen"])
    assert result.exit_code != 0
    assert "Frozen synchronization failed" in result.output
    assert not target.exists()
    assert (project / "guidelines.lock.json").read_bytes() == lock_before


def test_extensionless_remote_directory_update_discovers_nested_guideline(
    tmp_path: Path, monkeypatch
) -> None:
    """Update keeps an extensionless directory declaration discoverable."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "guidelines.yml").write_text(
        "version: 1\nguidelines:\n"
        "- source: gsemet/ai-guidelines-registry\n"
        "  path: SWE/Git/Git_Commit_Message\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    from ai_guidelines.fetch import AcquiredSource
    from ai_guidelines.update import build_update_plan

    source_root = tmp_path / "source"
    selected = source_root / "SWE/Git/Git_Commit_Message"
    selected.mkdir(parents=True)
    (selected / "git-commit-message.guidelines.md").write_text("commit\n", encoding="utf-8")

    class Fetcher:
        def acquire(self, location):
            return _context(
                AcquiredSource(
                    location=location,
                    root=source_root,
                    path=selected,
                    commit="a" * 40,
                    reference_kind="branch",
                )
            )

    plan = build_update_plan(project, fetcher=Fetcher())
    assert plan.entries[0].files == 1
