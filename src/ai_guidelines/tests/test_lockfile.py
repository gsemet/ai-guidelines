"""Persistence tests for the neutral generated lockfile."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_guidelines.lockfile import load_lockfile, save_lockfile
from ai_guidelines.models import (
    GuidelineFileRecord,
    GuidelinesLock,
    GuidelinesLockEntry,
)

CAPTURED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
COMMIT = "91f0e8d7c6b5a4938271615141312110fedcba98"
HASH = "b1" * 32


def _lock() -> GuidelinesLock:
    """Return a complete lock document for persistence tests."""
    return GuidelinesLock(
        generated_at=CAPTURED_AT,
        manager_version="0.1.0",
        guidelines=[
            GuidelinesLockEntry(
                expression="https://github.com/example/repo/guidelines/",
                name="guidelines",
                source="https://github.com/example/repo/guidelines/",
                source_type="github",
                requested_ref="main",
                reference_kind="branch",
                resolved_ref="main",
                commit=COMMIT,
                captured_at=CAPTURED_AT,
                target_path=".agents//guidelines\\",
                files=[
                    GuidelineFileRecord(
                        source_path="guidelines/team.guideline.md",
                        target_path=".agents//guidelines\\team.guideline.md",
                        sha256=HASH,
                    )
                ],
            )
        ],
    )


def test_lockfile_round_trip_is_deterministic_and_normalizes_targets(tmp_path: Path) -> None:
    """Neutral metadata, UTC timestamps, and target paths survive JSON round trips."""
    path = tmp_path / "guidelines.lock.json"

    save_lockfile(path, _lock())
    first = path.read_text(encoding="utf-8")
    loaded = load_lockfile(path)
    save_lockfile(path, loaded)

    assert path.read_text(encoding="utf-8") == first
    assert '"manager": "ai-guidelines"' in first
    assert '"lock_format": "ai-guidelines"' in first
    assert '"lock_format_version": 1' in first
    assert ".agents//guidelines" not in first
    assert '".agents/guidelines/team.guideline.md"' in first
    assert "2026-08-16T12:00:00Z" in first


def test_lockfile_rejects_foreign_documents_and_unsupported_major(tmp_path: Path) -> None:
    """Load errors explain compatibility boundaries without naming other tools."""
    path = tmp_path / "guidelines.lock.json"
    path.write_text('{"version": 1, "other_tool_version": "0.35.0"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unrecognized lockfile format"):
        load_lockfile(path)

    path.write_text(
        '{"version": 1, "lock_format": "ai-guidelines", "lock_format_version": 2}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="major|unsupported"):
        load_lockfile(path)


def test_lockfile_preserves_previous_bytes_when_replacement_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed replacement leaves the previous lockfile untouched."""
    path = tmp_path / "guidelines.lock.json"
    save_lockfile(path, GuidelinesLock(generated_at=CAPTURED_AT, manager_version="0.1.0"))
    original = path.read_bytes()

    def fail_replace(*args: object) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr("ai_guidelines.atomic.os.replace", fail_replace)

    with pytest.raises(OSError, match="atomically"):
        save_lockfile(path, _lock())

    assert path.read_bytes() == original
