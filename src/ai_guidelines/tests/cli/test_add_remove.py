"""CLI contract tests for declaration mutation."""

from pathlib import Path

from click.testing import CliRunner

from ai_guidelines.cli import guidelines


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "team.guidelines.md").write_text("team\n", encoding="utf-8")
    return source


def test_add_persists_normalized_declaration_and_syncs(tmp_path: Path, monkeypatch) -> None:
    """Add immediately materializes a selected source and lock state."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        guidelines,
        ["add", str(source), "team", "--alias", "team-guide", "--target-path", ".docs"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "added" in result.output
    assert (tmp_path / ".docs/team.guidelines.md").exists()
    manifest = (tmp_path / "guidelines.yml").read_text(encoding="utf-8")
    assert "alias: team-guide" in manifest
    assert "target_path: .docs" in manifest


def test_remove_ambiguous_and_missing_identifiers_do_not_write_or_delete_files(
    tmp_path: Path, monkeypatch
) -> None:
    """Removal errors leave declarations and synchronized files untouched."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "guidelines.yml"
    manifest.write_text(
        f"version: 1\nguidelines:\n- source: {source}\n  alias: duplicate-a\n"
        f"- source: {source}\n  alias: duplicate-b\n",
        encoding="utf-8",
    )
    before = manifest.read_bytes()
    runner = CliRunner()

    missing = runner.invoke(guidelines, ["remove", "missing"])
    ambiguous = runner.invoke(guidelines, ["remove", source.name])

    assert missing.exit_code != 0
    assert ambiguous.exit_code != 0
    assert manifest.read_bytes() == before
    assert not (tmp_path / ".github/guidelines").exists()


def test_remove_keeps_synchronized_files(tmp_path: Path, monkeypatch) -> None:
    """Successful removal changes only the manifest declaration."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["add", str(source), "--alias", "team"]).exit_code == 0
    target = tmp_path / ".github/guidelines/team.guidelines.md"
    assert target.exists()

    result = runner.invoke(guidelines, ["remove", "team"], catch_exceptions=False)

    assert result.exit_code == 0
    assert target.exists()
    assert "No guideline files were deleted or modified." in result.output


def test_add_supports_ref_and_all_selector_forms(tmp_path: Path, monkeypatch) -> None:
    """Add accepts a ref, literal path, stem pattern, and target/alias options."""
    source = _source(tmp_path)
    second_source = tmp_path / "second-source.guideline.md"
    second_source.write_text("other\n", encoding="utf-8")
    (source / "other.guideline.md").write_text("other\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["add", str(source), "team", "--ref", "main"]).exit_code == 0
    assert (
        runner.invoke(
            guidelines,
            [
                "add",
                str(second_source),
                "--alias",
                "other",
                "--target-path",
                ".alt",
            ],
        ).exit_code
        == 0
    )
    manifest = (tmp_path / "guidelines.yml").read_text(encoding="utf-8")
    assert "ref: main" in manifest
    assert "pattern: team" in manifest
    assert "source: " in manifest
    assert (tmp_path / ".alt/second-source.guidelines.md").exists()


def test_add_literal_path_ref_and_target_are_normalized_and_eager(
    tmp_path: Path, monkeypatch
) -> None:
    """A literal path is persisted distinctly from a basename pattern."""
    monkeypatch.chdir(tmp_path)
    from ai_guidelines import cli

    monkeypatch.setattr(cli, "sync_manifest", lambda _project: None)
    result = CliRunner().invoke(
        guidelines,
        [
            "add",
            "github/example/repository",
            "nested/team",
            "--ref",
            "main",
            "--target-path",
            ".docs/",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    manifest = (tmp_path / "guidelines.yml").read_text(encoding="utf-8")
    assert "path: nested/team.guidelines.md" in manifest
    assert "ref: main" in manifest
    assert "target_path: .docs" in manifest


def _run_add_selector(tmp_path: Path, monkeypatch, arguments: list[str], expected: str) -> None:
    """Run one selector form in an isolated project and verify eager materialization."""
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "team.guidelines.md").write_text("team\n", encoding="utf-8")
    (source / "nested/team.guideline.md").write_text("nested\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from ai_guidelines import cli

    monkeypatch.setattr(cli, "sync_manifest", lambda _project: None)
    result = CliRunner().invoke(guidelines, ["add", "github/example/repository", *arguments])
    assert result.exit_code == 0, result.output
    manifest = (tmp_path / "guidelines.yml").read_text(encoding="utf-8")
    assert expected.removesuffix(".guidelines.md").removesuffix(".guideline.md") in manifest


def test_add_literal_selector(tmp_path: Path, monkeypatch) -> None:
    """A literal filename selector is accepted independently."""
    _run_add_selector(tmp_path, monkeypatch, ["team.guidelines.md"], "team.guidelines.md")


def test_add_plural_selector(tmp_path: Path, monkeypatch) -> None:
    """A repeated exact path selector is accepted independently."""
    _run_add_selector(
        tmp_path, monkeypatch, ["--path", "nested/team.guideline.md"], "team.guideline.md"
    )


def test_add_wildcard_selector(tmp_path: Path, monkeypatch) -> None:
    """A filename glob selector is accepted independently."""
    _run_add_selector(tmp_path, monkeypatch, ["team*"], "team.guidelines.md")


def test_add_selector_with_ref(tmp_path: Path, monkeypatch) -> None:
    """A selector and revision option are persisted together."""
    _run_add_selector(tmp_path, monkeypatch, ["team", "--ref", "main"], "team.guidelines.md")


def test_add_plural_paths_rejects_mixed_positional_selector(tmp_path: Path, monkeypatch) -> None:
    """Plural selectors cannot be silently combined with the legacy pattern."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        guidelines,
        ["add", str(source), "team", "--path", "team.guidelines.md"],
    )
    assert result.exit_code != 0
    assert "cannot be combined" in result.output


def test_add_local_literal_selector_eagerly_materializes(tmp_path: Path, monkeypatch) -> None:
    """A local literal selector is persisted and copied immediately."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(guidelines, ["add", str(source), "team.guidelines.md"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github/guidelines/team.guidelines.md").read_text() == "team\n"


def test_add_singular_literal_selector_writes_plural_suffix(tmp_path: Path, monkeypatch) -> None:
    """A singular source filename selects either suffix and writes the plural form."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(guidelines, ["add", str(source), "team.guideline.md"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github/guidelines/team.guidelines.md").is_file()
    assert not (tmp_path / ".github/guidelines/team.guideline.md").exists()


def test_add_extensionless_literal_selector_finds_plural_source(
    tmp_path: Path, monkeypatch
) -> None:
    """An extensionless literal selector finds the plural source filename."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(guidelines, ["add", str(source), "team"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github/guidelines/team.guidelines.md").is_file()


def test_add_local_plural_selector_eagerly_materializes(tmp_path: Path, monkeypatch) -> None:
    """A local --path selector is eagerly synchronized as its selected file."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(guidelines, ["add", str(source), "--path", "team.guidelines.md"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github/guidelines/team.guidelines.md").exists()


def test_remove_accepts_display_and_canonical_identifiers(tmp_path: Path, monkeypatch) -> None:
    """Removal resolves both display names and canonical local source identities."""
    source = _source(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(guidelines, ["add", str(source)]).exit_code == 0
    assert runner.invoke(guidelines, ["remove", source.name]).exit_code == 0
    assert runner.invoke(guidelines, ["add", str(source)]).exit_code == 0
    assert runner.invoke(guidelines, ["remove", str(source.resolve())]).exit_code == 0
