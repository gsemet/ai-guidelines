"""Contract tests for the public manifest and lockfile models."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_guidelines import main
from ai_guidelines.locations import parse_location
from ai_guidelines.models import (
    GuidelineDeclaration,
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
    GuidelinesManifest,
    SourceIdentity,
)

CAPTURED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
COMMIT = "91f0e8d7c6b5a4938271615141312110fedcba98"
HASH = "b1" * 32


def test_public_entry_point_accepts_click_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    """The installed command can render help through the public entry point."""
    with pytest.raises(SystemExit) as exit_info:
        main("--help")

    assert exit_info.value.code == 0
    assert "Manage reusable project-owned Markdown guidelines." in capsys.readouterr().out


def test_manifest_scalar_and_object_entries_have_one_normalized_contract() -> None:
    """Scalar entries become objects without changing their source spelling."""
    manifest = GuidelinesManifest.model_validate(
        {
            "version": 1,
            "guidelines": [
                "github/example/guidelines/",
                {
                    "source": "./shared/guidelines/",
                    "ref": "main",
                    "paths": ["python/*.guideline.md"],
                    "target_path": ".agents\\guidelines\\",
                    "alias": "team-guidelines",
                },
            ],
        }
    )

    assert manifest.guidelines[0] == GuidelineDeclaration(source="github/example/guidelines/")
    assert manifest.guidelines[1].selected_paths == ["python/*.guideline.md"]
    assert manifest.guidelines[1].normalized_target_path == ".agents/guidelines"
    assert manifest.model_dump(exclude_none=True)["guidelines"][0] == {
        "source": "github/example/guidelines/"
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("alias", "not a safe alias"),
        ("pattern", "nested/*.md"),
        ("path", "../outside"),
        ("paths", ["/outside"]),
        ("paths", ["nested/../outside"]),
        ("target_path", "../outside"),
        ("target_path", r"C:\\outside"),
        ("ref", "--upload-pack=evil"),
        ("ref", "main\nother"),
        ("source", "https://user:secret@example.com/repo"),
        ("source", "https://example.com/repo?token=secret"),
    ],
)
def test_manifest_rejects_unsafe_values_without_echoing_input(
    field_name: str, value: object
) -> None:
    """Unsafe declaration data is rejected without exposing sensitive input."""
    values: dict[str, object] = {"source": "github/example/repo"}
    values[field_name] = value

    with pytest.raises(ValidationError) as error:
        GuidelineDeclaration.model_validate(values)

    rendered = str(error.value)
    assert "secret" not in rendered
    assert "upload-pack" not in rendered


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/repo?token=",
        "https://example.com/repo?password",
        "https://github.com/org/repo/tree/main/../secret",
        "https://github.com/org/repo/tree/main/guidelines/*",
        "https://gitlab.example.com/team/repo/-/tree/main/../secret",
        "https://gitlab.example.com/team/repo#main:../secret",
        "https://example.com/team/repo#main:guidelines/*",
        "git@github.com:team/repo/../secret",
        "git@github.com:team/repo/*",
        "git@github.com:team/repo?token=",
        "github/example/repo\n",
        "../outside",
    ],
)
def test_manifest_rejects_unsafe_source_expressions(source: str) -> None:
    """Source credentials, traversal, and wildcard scopes fail validation."""
    with pytest.raises(ValidationError) as error:
        GuidelineDeclaration(source=source)

    assert "token" not in str(error.value).lower()
    assert "password" not in str(error.value).lower()
    assert "../outside" not in str(error.value)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://gitlab.example.com/team/repo#main:guidelines/",
            "https://gitlab.example.com/team/repo/guidelines",
        ),
        (
            "https://example.com/team/repo:guidelines/",
            "https://example.com/team/repo/guidelines",
        ),
    ],
)
def test_manifest_source_identity_retains_repository_path_selection(
    source: str, expected: str
) -> None:
    """Fragment and compact source paths remain part of canonical identity."""
    declaration = GuidelineDeclaration(source=source)

    assert declaration.canonical_source == expected


def test_github_declaration_location_and_lock_identities_are_coherent() -> None:
    """Every supported GitHub spelling resolves to one lock-matchable identity."""
    sources = [
        "github/example-repo",
        "github/example-repo#main",
        "github/example-repo/guidelines/",
        "microsoft/example-repo",
        "https://github.com/microsoft/example-repo",
        "https://github.com/microsoft/example-repo/tree/main/guidelines/",
    ]

    for source in sources:
        declaration = GuidelineDeclaration(source=source)
        location = parse_location(source)
        entry = GuidelinesLockEntry(
            expression=declaration.source,
            name=declaration.display_name,
            source=location.canonical_source,
            source_type=location.source_type,
            requested_ref=location.requested_ref,
        )
        lock = GuidelinesLock(guidelines=[entry])

        assert declaration.canonical_source == location.canonical_source
        assert lock.find_entry(declaration) is entry


def test_identity_separates_canonical_source_display_name_and_alias() -> None:
    """Source identity is stable and presentation metadata remains distinct."""
    declaration = GuidelineDeclaration(
        source=(
            "https://GitHub.com/example/repo/tree/main/"
            "git-commit-message.guideline.md?ref_type=heads"
        ),
        alias="team-guidelines",
    )

    identity = declaration.identity

    assert isinstance(identity, SourceIdentity)
    assert identity.canonical_source == (
        "https://github.com/example/repo/git-commit-message.guideline.md"
    )
    assert identity.display_name == "git-commit-message"
    assert identity.alias == "team-guidelines"


def test_manifest_binds_relative_sources_to_the_manifest_directory(tmp_path: Path) -> None:
    """Relative local source identity stays stable after the CWD changes."""
    project = tmp_path / "project"
    source = project / "shared" / "guidelines"
    source.mkdir(parents=True)

    manifest = GuidelinesManifest.model_validate(
        {"guidelines": ["./shared/guidelines/"]}
    ).bind_source_base(project)

    assert manifest.guidelines[0].canonical_source == source.as_posix()


def test_lock_entry_records_provenance_and_managed_hashes() -> None:
    """A complete neutral entry contains replay and ownership information."""
    declaration = GuidelineDeclaration(
        source="https://github.com/example/repo/guidelines/",
        ref="main",
        pattern="team_*",
        target_path=".agents/guidelines/",
        alias="team-guidelines",
    )
    entry = GuidelinesLockEntry(
        expression=declaration.source,
        name=declaration.display_name,
        source=declaration.canonical_source,
        source_type="github",
        requested_ref=declaration.ref,
        reference_kind="branch",
        resolved_ref="main",
        commit=COMMIT,
        captured_at=CAPTURED_AT,
        pattern=declaration.pattern,
        target_path=declaration.target_path,
        alias=declaration.alias,
        files=[
            GuidelineFileRecord(
                source_path="guidelines/team.guideline.md",
                target_path=".agents/guidelines/team.guideline.md",
                sha256=HASH.upper(),
            )
        ],
    )
    lock = GuidelinesLock(
        generated_at=CAPTURED_AT,
        manager_version="0.1.0",
        guidelines=[entry],
    )

    assert lock.manager == "ai-guidelines"
    assert lock.lock_format == "ai-guidelines"
    assert lock.lock_format_version == 1
    assert entry.captured_at == CAPTURED_AT
    assert entry.files[0].sha256 == HASH
    assert lock.is_complete_for(declaration)


def test_lock_lookup_checks_all_declaration_specific_entries() -> None:
    """A later matching entry is not hidden by an earlier source match."""
    declaration = GuidelineDeclaration(
        source="https://github.com/example/repo/guidelines/",
        ref="main",
        pattern="team_*",
        target_path=".agents/guidelines/",
        alias="team-guidelines",
    )
    matching = GuidelinesLockEntry(
        expression=declaration.source,
        name=declaration.display_name,
        source=declaration.canonical_source,
        source_type="github",
        requested_ref=declaration.ref,
        resolved_ref="main",
        commit=COMMIT,
        captured_at=CAPTURED_AT,
        pattern=declaration.pattern,
        target_path=declaration.target_path,
        alias=declaration.alias,
        files=[
            GuidelineFileRecord(
                source_path="team.guideline.md",
                target_path=".agents/guidelines/team.guideline.md",
                sha256=HASH,
            )
        ],
    )
    lock = GuidelinesLock(
        generated_at=CAPTURED_AT,
        manager_version="0.1.0",
        guidelines=[matching.model_copy(update={"pattern": "python_*"}), matching],
    )

    assert lock.find_entry(declaration) == matching
    assert lock.is_complete_for(declaration)


def test_incomplete_lock_entries_are_detectable() -> None:
    """Missing revision or managed hashes never count as frozen-complete."""
    declaration = GuidelineDeclaration(source="https://github.com/example/repo")
    entry = GuidelinesLockEntry(
        expression=declaration.source,
        name=declaration.display_name,
        source=declaration.canonical_source,
        source_type="github",
        resolved_ref="main",
        files=[
            GuidelineFileRecord(
                source_path="guide.guideline.md",
                target_path=".github/guidelines/guide.guideline.md",
            )
        ],
    )
    lock = GuidelinesLock(generated_at=CAPTURED_AT, guidelines=[entry])

    assert not lock.is_complete_for(declaration)
    assert lock.missing_or_incomplete(declaration) is entry

    complete = GuidelinesLockEntry(
        expression=declaration.source,
        name=declaration.display_name,
        source=declaration.canonical_source,
        source_type="github",
        resolved_ref="main",
        commit=COMMIT,
        files=[
            GuidelineFileRecord(
                source_path="guide.guideline.md",
                target_path=".github/guidelines/guide.guideline.md",
                sha256=HASH,
            )
        ],
    )
    lock_with_missing_target = GuidelinesLock(
        generated_at=CAPTURED_AT,
        guidelines=[complete.model_copy(update={"target_path": None})],
    )

    assert not lock_with_missing_target.is_complete_for(declaration)


@pytest.mark.parametrize(
    "field_name",
    ["requested_ref", "resolved_ref", "resolved_version", "semver_constraint", "resolved_tag"],
)
def test_lock_rejects_unsafe_revision_metadata(field_name: str) -> None:
    """Every persisted revision field rejects option and control input."""
    values: dict[str, object] = {
        "expression": "github/example/repo",
        "name": "guidelines",
        "source": "github/example/repo",
        "source_type": "github",
        "resolved_ref": "main",
        "commit": COMMIT,
    }
    values[field_name] = "main\n--upload-pack=evil"

    with pytest.raises(ValidationError) as error:
        GuidelinesLockEntry.model_validate(values)

    assert "upload-pack" not in str(error.value)


def test_lock_rejects_foreign_documents_and_unsupported_major() -> None:
    """Foreign lockfiles and unknown major formats fail explicitly and generically."""
    with pytest.raises(ValidationError, match="unrecognized lockfile format"):
        GuidelinesLock.model_validate({"version": 1, "other_tool_version": "0.35.0"})

    with pytest.raises(ValidationError, match="major|unsupported"):
        GuidelinesLock.model_validate(
            {"version": 1, "lock_format": "ai-guidelines", "lock_format_version": 2}
        )


def test_unknown_safe_lock_metadata_is_ignored_for_current_major() -> None:
    """Forward-compatible metadata does not alter the supported contract."""
    lock = GuidelinesLock.model_validate(
        {
            "version": 1,
            "manager": "ai-guidelines",
            "lock_format": "ai-guidelines",
            "lock_format_version": 1,
            "future_metadata": {"display_hint": "safe"},
        }
    )

    assert not hasattr(lock, "future_metadata")


def test_models_reject_naive_timestamps_and_invalid_hashes() -> None:
    """Managed records require timezone-aware capture times and SHA-256 hashes."""
    with pytest.raises(ValueError):
        GuidelineFileRecord(source_path="guide.md", target_path="guide.md", sha256="not-a-sha256")

    with pytest.raises(ValueError):
        GuidelinesLockEntry(
            expression="./guidelines/",
            name="guidelines",
            source="./guidelines/",
            source_type="local",
            resolved_ref="working-tree",
            captured_at=datetime(2026, 8, 16, 12, 0),
        )
