"""Persistence tests for the project manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_guidelines.manifest import load_manifest, save_manifest
from ai_guidelines.models import GuidelineDeclaration, GuidelinesManifest


def test_manifest_round_trip_is_deterministic_and_normalized(tmp_path: Path) -> None:
    """Manifest persistence writes stable object-form YAML."""
    path = tmp_path / "guidelines.yml"
    manifest = GuidelinesManifest.model_validate(
        {
            "default_guidelines_path": ".custom/guidelines/",
            "guidelines": [
                "github/example/repo/guidelines/",
                GuidelineDeclaration(
                    source="./shared/guidelines/",
                    pattern="team_*",
                    target_path=".github/guidelines/",
                ),
            ],
        }
    )

    save_manifest(path, manifest)
    first = path.read_text(encoding="utf-8")
    loaded = load_manifest(path)
    save_manifest(path, loaded)

    assert path.read_text(encoding="utf-8") == first
    assert loaded.version == 1
    assert loaded.normalized_default_guidelines_path == ".custom/guidelines"
    assert "- source: github/example/repo/guidelines/" in first


def test_manifest_rejects_unknown_fields_and_invalid_versions(tmp_path: Path) -> None:
    """Malformed structural data fails before publication."""
    with pytest.raises(ValidationError):
        GuidelinesManifest.model_validate({"version": 2, "guidelines": [], "unexpected": True})

    path = tmp_path / "guidelines.yml"
    path.write_text("version: 2\nguidelines: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="guidelines.yml"):
        load_manifest(path)


def test_manifest_save_replaces_existing_file_atomically(tmp_path: Path) -> None:
    """A successful save replaces the complete previous document."""
    path = tmp_path / "guidelines.yml"
    path.write_text("version: 1\nguidelines: []\n", encoding="utf-8")

    save_manifest(
        path,
        GuidelinesManifest(guidelines=[GuidelineDeclaration(source="./new-guidelines/")]),
    )

    assert path.read_text(encoding="utf-8") == (
        "version: 1\nguidelines:\n- source: ./new-guidelines/\n"
    )


def test_manifest_preserves_previous_bytes_when_serialization_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed serialization cannot truncate an existing manifest."""
    path = tmp_path / "guidelines.yml"
    original = b"version: 1\nguidelines: []\n"
    path.write_bytes(original)

    def fail_dump(*args: object, **kwargs: object) -> str:
        raise TypeError("serialization failed")

    monkeypatch.setattr("ai_guidelines.manifest.yaml.safe_dump", fail_dump)

    with pytest.raises(TypeError, match="serialization failed"):
        save_manifest(path, GuidelinesManifest())

    assert path.read_bytes() == original
