"""Tests for provider-neutral guideline source location parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_guidelines.locations import LocationParseError, SourceLocation, parse_location


def test_gitlab_blob_url_parses_file_and_revision() -> None:
    location = parse_location(
        "https://gitlab.example.com/team/tools/guidelines/-/blob/"
        "main/docs/team.guideline.md?ref_type=heads"
    )

    assert location.source_type == "gitlab"
    assert location.repository == "https://gitlab.example.com/team/tools/guidelines"
    assert location.relative_path == "docs/team.guideline.md"
    assert location.requested_ref == "main"
    assert location.kind == "file"
    assert location.canonical_source.endswith("/docs/team.guideline.md")


def test_provider_tree_urls_share_one_contract() -> None:
    gitlab = parse_location("https://gitlab.example.com/team/repo/-/tree/release/guidelines/")
    github = parse_location("https://github.com/example/repo/tree/main/guidelines/")

    assert gitlab.source_type == "gitlab"
    assert gitlab.kind == github.kind == "folder"
    assert gitlab.requested_ref == "release"
    assert github.repository == "https://github.com/example/repo"
    assert github.relative_path == "guidelines"


def test_repository_fragment_and_compact_path_preserve_selection() -> None:
    fragment = parse_location("https://example.com/team/repo#main:guidelines/")
    compact = parse_location("https://example.com/team/repo:guidelines/Engineering")

    assert fragment.requested_ref == "main"
    assert fragment.relative_path == "guidelines"
    assert compact.repository == "https://example.com/team/repo"
    assert compact.relative_path == "guidelines/Engineering"
    assert compact.requested_ref is None


def test_github_short_forms_and_direct_ssh_are_supported() -> None:
    short = parse_location("github/example-repo/guidelines/")
    versioned = parse_location("microsoft/example-repo#v1.0.0")
    ssh = parse_location("git@example.com:team/repo.git#main:guidelines/")

    assert short.repository == "https://github.com/example-repo"
    assert short.relative_path == "guidelines"
    assert versioned.repository == "https://github.com/microsoft/example-repo"
    assert versioned.requested_ref == "v1.0.0"
    assert versioned.relative_path == "."
    assert ssh.source_type == "git"
    assert ssh.repository == "git@example.com:team/repo.git"
    assert ssh.relative_path == "guidelines"


def test_local_file_and_folder_locations_resolve_against_base(tmp_path: Path) -> None:
    folder = tmp_path / "shared" / "guidelines"
    folder.mkdir(parents=True)
    file_path = folder / "team.guideline.md"
    file_path.write_text("# Team\n", encoding="utf-8")

    folder_location = parse_location("./shared/guidelines/", base_dir=tmp_path)
    file_location = parse_location(str(file_path), base_dir=tmp_path)

    assert folder_location.source_type == "local"
    assert folder_location.local_path == folder.resolve()
    assert folder_location.kind == "folder"
    assert file_location.kind == "file"
    assert file_location.relative_path == "team.guideline.md"


@pytest.mark.parametrize(
    "expression",
    [
        "https://user:secret@example.com/repo.git",
        "https://example.com/repo?token=",
        "https://example.com/repo?password",
        "https://example.com/repo#token=secret",
        "https://github.com/org/repo/tree/main/../secret",
        "https://github.com/org/repo/tree/main/guidelines/*",
        "https://gitlab.example.com/org/repo/-/tree/main/../secret",
        "git@example.com:team/repo/../secret",
        "git@example.com:team/repo/*",
        "github/example/repo\n",
        "../outside",
    ],
)
def test_unsafe_locations_are_rejected_without_leaking_input(expression: str) -> None:
    with pytest.raises(LocationParseError) as error:
        parse_location(expression)

    rendered = str(error.value).lower()
    assert "secret" not in rendered
    assert "token" not in rendered
    assert "../outside" not in rendered


@pytest.mark.parametrize(
    "revision",
    ["--upload-pack=evil", "main\nother", "main\r", "main\t", "main\x00"],
)
def test_unsafe_revision_override_is_rejected(revision: str) -> None:
    with pytest.raises(LocationParseError, match="revision|printable|safe") as error:
        parse_location("https://example.com/team/repo#main:guidelines/", ref=revision)

    assert "upload-pack" not in str(error.value)
    assert "\n" not in str(error.value)


def test_source_location_public_model_rejects_credentials() -> None:
    with pytest.raises(ValidationError, match="credentials") as error:
        SourceLocation(
            expression="git://example.com/team/repo",
            source_type="git",
            repository="git://user:secret@example.com/team/repo",
            relative_path=".",
            kind="folder",
            canonical_source="git://example.com/team/repo",
        )

    assert "secret" not in str(error.value)


def test_relative_local_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LocationParseError, match="within|escape"):
        parse_location("./link", base_dir=tmp_path)


def test_explicit_base_keeps_local_identity_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    source = project / "shared" / "guidelines"
    source.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.chdir(project)
    first = parse_location("./shared/guidelines/")
    monkeypatch.chdir(elsewhere)
    second = parse_location("./shared/guidelines/", base_dir=project)

    assert first.canonical_source == second.canonical_source
    assert first.local_path == second.local_path == source.resolve()
