"""Tests for same-directory atomic writes and advisory locking."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_guidelines.atomic import AtomicWriteError, advisory_lock, atomic_write


def test_atomic_write_handles_text_and_bytes(tmp_path: Path) -> None:
    """Text and byte payloads replace a destination as complete documents."""
    path = tmp_path / "nested" / "file.txt"

    atomic_write(path, "first\n")
    assert path.read_text(encoding="utf-8") == "first\n"

    atomic_write(path, b"second\n")
    assert path.read_bytes() == b"second\n"


def test_atomic_write_preserves_previous_bytes_when_replacement_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Replacement errors are wrapped and cannot destroy the old destination."""
    path = tmp_path / "file.txt"
    path.write_bytes(b"previous\n")

    def fail_replace(*args: object) -> None:
        raise OSError("no replacement")

    monkeypatch.setattr("ai_guidelines.atomic.os.replace", fail_replace)

    with pytest.raises(AtomicWriteError, match="atomically"):
        atomic_write(path, "new\n")

    assert path.read_bytes() == b"previous\n"


def test_advisory_lock_serializes_one_operation(tmp_path: Path) -> None:
    """The public lock context can guard a project operation."""
    lock_path = tmp_path / "state" / "guidelines.lock"

    with advisory_lock(lock_path):
        assert lock_path.exists()

    assert lock_path.exists()
    with advisory_lock(lock_path):
        pass
