"""Tests for safe local and remote guideline acquisition."""

from __future__ import annotations

import subprocess
import traceback
from collections.abc import Sequence
from pathlib import Path

import pytest

from ai_guidelines.discovery import discover_guidelines
from ai_guidelines.fetch import (
    SourceFetcher,
    SourceFetchError,
    _SourcePathNotFoundError,
    acquire_source,
)
from ai_guidelines.locations import SourceLocation, parse_location


def test_remote_folder_uses_argument_arrays_and_sparse_checkout(tmp_path: Path) -> None:
    commands: list[tuple[list[str], Path | None]] = []

    def runner(args: Sequence[str], cwd: Path | None = None) -> str:
        command = list(args)
        commands.append((command, cwd))
        if command[:2] == ["ls-remote", "--heads"]:
            return "a\trefs/heads/main\n"
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guidelines" / "nested").mkdir(parents=True)
            (checkout / "guidelines" / "nested" / "team.guidelines.md").write_text(
                "# Team\n", encoding="utf-8"
            )
        if command[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        assert acquired.path.is_dir()
        assert acquired.path.name == "guidelines"
        assert acquired.root.parent == tmp_path
        assert acquired.commit == "a" * 40

    clone = next(command for command, _ in commands if command[0] == "clone")
    sparse = next(command for command, _ in commands if command[:2] == ["sparse-checkout", "set"])
    assert "--no-checkout" in clone
    assert "--filter=blob:none" in clone
    assert clone[-2] == "https://example.com/team/repo"
    assert "--no-cone" in sparse
    assert any(argument.startswith("guidelines") for argument in sparse)


def test_locked_revision_is_checked_out_before_return(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        commands.append(command)
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guide.guideline.md").write_text("# Guide\n", encoding="utf-8")
        if command[:2] == ["rev-parse", "HEAD"]:
            return "b" * 40
        return ""

    location = parse_location("https://example.com/team/repo#91f0e8d")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        assert acquired.resolved_ref == "91f0e8d"

    assert ["checkout", "--detach", "91f0e8d"] in commands


def test_batch_acquisition_unions_sparse_paths_and_clones_once(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        commands.append(command)
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guidelines").mkdir(parents=True)
            for name in ("one", "two"):
                (checkout / "guidelines" / f"{name}.guidelines.md").write_text(
                    name, encoding="utf-8"
                )
        if command[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    locations = [
        parse_location("https://example.com/team/repo#main:guidelines/one.guidelines.md"),
        parse_location("https://example.com/team/repo#main:guidelines/two.guidelines.md"),
    ]
    with SourceFetcher(runner=runner, temp_root=tmp_path).acquire_many(locations) as acquired:
        assert [item.path.name for item in acquired] == [
            "one.guidelines.md",
            "two.guidelines.md",
        ]
        assert acquired[0].root == acquired[1].root

    assert len([command for command in commands if command[0] == "clone"]) == 1
    sparse = next(command for command in commands if command[:2] == ["sparse-checkout", "set"])
    assert "guidelines/one.guidelines.md" in sparse
    assert "guidelines/two.guidelines.md" in sparse


def test_semver_range_resolves_highest_matching_tag(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        commands.append(command)
        if command[:2] == ["ls-remote", "--tags"]:
            return "a\trefs/tags/v1.2.0\nb\trefs/tags/v1.5.0\nc\trefs/tags/v2.0.0\n"
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guidelines").mkdir(parents=True)
            (checkout / "guidelines" / "team.guideline.md").write_text("# Team\n", encoding="utf-8")
        if command[:2] == ["rev-parse", "HEAD"]:
            return "c" * 40
        return ""

    location = parse_location("https://example.com/team/repo#>=1.0,<2.0:guidelines/")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        assert acquired.resolved_ref == "v1.5.0"

    clone = next(command for command in commands if command[0] == "clone")
    assert "--branch" in clone
    assert "v1.5.0" in clone


def test_zero_major_caret_range_stays_within_minor_version(tmp_path: Path) -> None:
    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        if command[:2] == ["ls-remote", "--tags"]:
            return "a\trefs/tags/v0.2.0\nb\trefs/tags/v0.2.9\nc\trefs/tags/v0.3.0\n"
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guidelines").mkdir(parents=True)
            (checkout / "guidelines" / "team.guideline.md").write_text("team", encoding="utf-8")
        if command[:2] == ["rev-parse", "HEAD"]:
            return "d" * 40
        return ""

    location = parse_location("https://example.com/team/repo#^0.2.0:guidelines/")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        assert acquired.resolved_ref == "v0.2.9"


def test_local_source_uses_the_same_contract(tmp_path: Path) -> None:
    folder = tmp_path / "local-guidelines"
    folder.mkdir()
    location = parse_location(str(folder))

    with acquire_source(location) as acquired:
        assert acquired.path == folder.resolve()
        assert acquired.location == location
        assert not acquired.temporary
        assert acquired.commit is None


def test_local_git_source_reports_head(tmp_path: Path) -> None:
    folder = tmp_path / "local-guidelines"
    folder.mkdir()
    location = parse_location(str(folder))
    commands: list[tuple[list[str], Path | None]] = []

    def runner(args: Sequence[str], cwd: Path | None = None) -> str:
        commands.append((list(args), cwd))
        return "c" * 40

    with acquire_source(location, runner=runner) as acquired:
        assert acquired.commit == "c" * 40

    assert commands == [(["rev-parse", "HEAD"], folder)]


def test_remote_failure_hides_diagnostics_and_cleans_checkout(tmp_path: Path) -> None:
    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        raise RuntimeError("fatal: token=super-secret-value")

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    with (
        pytest.raises(SourceFetchError) as error,
        acquire_source(location, runner=runner, temp_root=tmp_path),
    ):
        pass

    assert "super-secret-value" not in str(error.value)
    assert "git clone" not in str(error.value)
    assert error.value.__cause__ is None
    assert not list(tmp_path.iterdir())


def test_remote_failure_traceback_does_not_retain_provider_exception(tmp_path: Path) -> None:
    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        raise RuntimeError("fatal: token=super-secret-value")

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    with (
        pytest.raises(SourceFetchError) as error,
        acquire_source(location, runner=runner, temp_root=tmp_path),
    ):
        pass

    formatted = "".join(
        traceback.format_exception(type(error.value), error.value, error.value.__traceback__)
    )
    assert error.value.__suppress_context__
    assert "super-secret-value" not in formatted


def test_missing_remote_path_has_actionable_safe_suggestion(tmp_path: Path) -> None:
    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "guidelines").mkdir(parents=True)
        if command[:2] == ["ls-tree", "-r"]:
            return "guidelines/Engineering/team.guideline.md\n"
        if command[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    location = parse_location("https://example.com/team/repo#main:Engineering/team.guideline.md")
    with (
        pytest.raises(_SourcePathNotFoundError) as error,
        acquire_source(location, runner=runner, temp_root=tmp_path),
    ):
        pass

    message = str(error.value)
    assert "repository root" in message
    assert "guidelines/Engineering/team.guideline.md" in message
    assert "#main:guidelines/Engineering/team.guideline.md" in message


def test_extensionless_remote_path_resolves_guideline_suffix(tmp_path: Path) -> None:
    """An extensionless remote file path resolves a plural guideline file."""

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        if command[0] == "clone":
            checkout = Path(command[-1])
            (checkout / "SWE/Git").mkdir(parents=True)
            (checkout / "SWE/Git/Git_Commit_Message.guidelines.md").write_text(
                "commit", encoding="utf-8"
            )
        if command[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    location = parse_location("https://example.com/team/repo#main:SWE/Git/Git_Commit_Message")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        assert acquired.path.name == "Git_Commit_Message.guidelines.md"


def test_extensionless_remote_directory_resolves_nested_guideline_file(tmp_path: Path) -> None:
    """An extensionless directory selector discovers nested guideline files."""

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        command = list(args)
        if command[0] == "clone":
            checkout = Path(command[-1])
            target = checkout / "SWE/Git/Git_Commit_Message"
            target.mkdir(parents=True)
            (target / "git-commit-message.guidelines.md").write_text("commit", encoding="utf-8")
        if command[:2] == ["rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    location = parse_location("https://example.com/team/repo#main:SWE/Git/Git_Commit_Message")
    with acquire_source(location, runner=runner, temp_root=tmp_path) as acquired:
        result = discover_guidelines(acquired)

    assert [item.filename for item in result.files] == ["git-commit-message.guidelines.md"]


def test_unsafe_revision_never_reaches_git_runner(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        commands.append(list(args))
        return ""

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    unsafe = location.model_copy(update={"requested_ref": "--upload-pack=evil"})
    with (
        pytest.raises(SourceFetchError, match="revision|safe"),
        acquire_source(unsafe, runner=runner, temp_root=tmp_path),
    ):
        pass

    assert commands == []


def test_tampered_source_location_is_rejected_before_git_runner(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        commands.append(list(args))
        return ""

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    tampered = location.model_copy(update={"relative_path": "../outside"})
    with (
        pytest.raises(SourceFetchError, match="path|safe|source"),
        acquire_source(tampered, runner=runner, temp_root=tmp_path),
    ):
        pass

    assert commands == []


def test_inconsistent_remote_location_is_rejected_before_git_runner(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        commands.append(list(args))
        return ""

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    tampered = location.model_copy(update={"repository": "https://example.com/other/repo"})
    with (
        pytest.raises(SourceFetchError, match="location|safe|source"),
        acquire_source(tampered, runner=runner, temp_root=tmp_path),
    ):
        pass

    assert commands == []


def test_inconsistent_local_location_is_rejected_before_access(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    location = parse_location(str(source))
    tampered = location.model_copy(update={"local_path": other})

    with pytest.raises(SourceFetchError, match="location|safe|source"), acquire_source(tampered):
        pass


def test_unsafe_sparse_patterns_never_reach_git_runner(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: Sequence[str], _cwd: Path | None = None) -> str:
        commands.append(list(args))
        return ""

    location = parse_location("https://example.com/team/repo#main:guidelines/")
    unsafe_inputs = (
        {"sparse_pattern": "../outside"},
        {"sparse_patterns": ["guidelines/../outside"]},
        {"sparse_patterns": ["--stdin"]},
        {"sparse_patterns": ["/absolute/path"]},
        {"sparse_patterns": ["guidelines/\x00.md"]},
        {"sparse_patterns": ["!guidelines"]},
    )

    for sparse_options in unsafe_inputs:
        with (
            pytest.raises(SourceFetchError, match="sparse"),
            acquire_source(
                location,
                runner=runner,
                temp_root=tmp_path,
                **sparse_options,
            ),
        ):
            pass

    assert commands == []


def _git(args: Sequence[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_remote_acquisition_materializes_exact_commit_from_local_git_remote(
    tmp_path: Path,
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    _git(["init", "--initial-branch=main"], cwd=working)
    _git(["config", "user.email", "test@example.com"], cwd=working)
    _git(["config", "user.name", "Test User"], cwd=working)
    guidelines = working / "guidelines"
    guidelines.mkdir()
    (guidelines / "team.guideline.md").write_text("# Team\n", encoding="utf-8")
    (working / "not-selected.txt").write_text("not selected\n", encoding="utf-8")
    _git(["add", "."], cwd=working)
    _git(["commit", "-m", "initial guideline"], cwd=working)
    _git(["tag", "v1.0.0"], cwd=working)
    expected_commit = _git(["rev-parse", "HEAD"], cwd=working)

    remote = tmp_path / "remote.git"
    _git(["clone", "--bare", str(working), str(remote)])
    remote_url = remote.resolve().as_uri()
    location = SourceLocation(
        expression=remote_url,
        source_type="git",
        repository=remote_url,
        relative_path="guidelines",
        requested_ref="v1.0.0",
        kind="folder",
        canonical_source=f"{remote_url}/guidelines",
    )

    with acquire_source(location, temp_root=tmp_path / "cache") as acquired:
        assert acquired.commit == expected_commit
        assert acquired.resolved_ref == "v1.0.0"
        assert acquired.path == acquired.root / "guidelines"
        assert (acquired.path / "team.guideline.md").read_text(encoding="utf-8") == "# Team\n"
        assert not (acquired.root / "not-selected.txt").exists()
