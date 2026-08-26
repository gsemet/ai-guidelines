"""CLI contract tests for cache maintenance without project manifests."""

from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.cli import guidelines
from ai_guidelines.locations import parse_location


def test_cache_commands_work_without_manifest(tmp_path: Path, monkeypatch) -> None:
    """Cache maintenance commands do not require guidelines.yml."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    directory = runner.invoke(guidelines, ["cache", "dir"])
    size = runner.invoke(guidelines, ["cache", "size"])
    clean = runner.invoke(guidelines, ["cache", "clean"])
    prune = runner.invoke(guidelines, ["cache", "prune"])

    assert directory.exit_code == 0
    assert size.exit_code == 0
    assert clean.exit_code == 0
    assert prune.exit_code == 0
    assert directory.output.strip()
    assert "Cache size:" in size.output
    assert "Removed" in clean.output
    assert "Pruned" in prune.output
    assert not (tmp_path / "guidelines.yml").exists()


def test_cache_output_is_capture_safe(tmp_path: Path, monkeypatch) -> None:
    """Cache maintenance output is useful and free of terminal control codes."""
    monkeypatch.chdir(tmp_path)
    for arguments in (("dir",), ("size",), ("clean",), ("prune",)):
        result = CliRunner().invoke(guidelines, ["cache", *arguments])
        assert result.exit_code == 0
        assert "\x1b[" not in result.output


def test_cache_size_captures_materialized_snapshot_without_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """Cache reporting includes captured source bytes and needs no project manifest."""
    cache_dir = tmp_path / "cache"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "guide.guideline.md").write_text("captured source\n", encoding="utf-8")
    location = parse_location("https://example.com/team/repo#main:guide.guideline.md")
    MaterializedGuidelineCache(cache_dir=cache_dir).write_snapshot(
        location,
        checkout,
        [location],
        resolved_ref="main",
        commit="a" * 40,
        reference_kind="branch",
    )
    monkeypatch.chdir(tmp_path)
    from ai_guidelines import cli

    monkeypatch.setattr(cli, "_cache", lambda: MaterializedGuidelineCache(cache_dir=cache_dir))
    result = CliRunner().invoke(guidelines, ["cache", "size"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Cache size:" in result.output
    assert int(result.output.split(":", 1)[1].split()[0]) > 0
