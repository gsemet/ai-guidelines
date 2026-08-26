"""Deterministic JSON persistence for ``guidelines.lock.json``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_guidelines.atomic import atomic_write
from ai_guidelines.models import GuidelinesLock


def _json_value(value: Any) -> Any:
    """Convert timestamps and nested values into deterministic JSON values."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _lockfile_payload(document: GuidelinesLock) -> dict[str, Any]:
    """Build a normalized payload while retaining explicit target filenames."""
    payload = document.model_dump(exclude_none=True)
    for entry_payload, entry in zip(payload["guidelines"], document.guidelines, strict=True):
        if entry.target_path is not None:
            entry_payload["target_path"] = entry.normalized_target_path
        for file_payload, record in zip(entry_payload["files"], entry.files, strict=True):
            file_payload["target_path"] = record.normalized_target_path
    return payload


def load_lockfile(path: Path | str) -> GuidelinesLock:
    """Load and validate a neutral lockfile.

    Args:
        path: Path to ``guidelines.lock.json``.

    Returns:
        A validated neutral lock document.

    Raises:
        FileNotFoundError: If the lockfile is absent.
        ValueError: If JSON, compatibility metadata, or entries are invalid.
    """
    destination = Path(path)
    try:
        raw = json.loads(destination.read_text(encoding="utf-8"))
        return GuidelinesLock.model_validate(raw or {})
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Invalid guideline lockfile {destination}: {type(exc).__name__}") from exc
    except ValidationError as exc:
        details = str(exc)
        if "unrecognized lockfile format" in details:
            message = "unrecognized lockfile format; regenerate it"
        elif "unsupported lock" in details:
            message = "unsupported lock format or manager metadata"
        else:
            message = "validation failed"
        raise ValueError(f"Invalid guideline lockfile {destination}: {message}") from exc


def save_lockfile(path: Path | str, lockfile: GuidelinesLock) -> None:
    """Validate and atomically save a normalized neutral lockfile.

    Args:
        path: Destination path for ``guidelines.lock.json``.
        lockfile: Lock document to persist.

    Raises:
        TypeError: If the document or destination type is unsupported.
        OSError: If atomic publication fails.
        pydantic.ValidationError: If the document is invalid.
    """
    if not isinstance(lockfile, GuidelinesLock):
        raise TypeError("lockfile must be a GuidelinesLock instance")
    if not isinstance(path, (Path, str)):
        raise TypeError("lockfile path must be a string or pathlib.Path")
    validated = GuidelinesLock.model_validate(lockfile.model_dump(exclude_none=True))
    content = (
        json.dumps(_json_value(_lockfile_payload(validated)), indent=2, sort_keys=False) + "\n"
    )
    atomic_write(Path(path), content)
