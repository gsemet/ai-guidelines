"""Deterministic YAML persistence for ``guidelines.yml``."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ai_guidelines.atomic import atomic_write
from ai_guidelines.models import GuidelinesManifest


def _yaml_value(value: Any) -> Any:
    """Convert model values to deterministic YAML-compatible values."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {key: _yaml_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_yaml_value(item) for item in value]
    return value


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Serialize before invoking the atomic replacement primitive."""
    content = yaml.safe_dump(
        _yaml_value(payload),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    atomic_write(path, content)


def load_manifest(path: Path | str) -> GuidelinesManifest:
    """Load and validate a manifest.

    Args:
        path: Path to ``guidelines.yml``.

    Returns:
        A scalar-normalized and project-bound manifest.

    Raises:
        FileNotFoundError: If the manifest is absent.
        ValueError: If YAML or manifest validation fails.
    """
    destination = Path(path)
    try:
        raw = yaml.safe_load(destination.read_text(encoding="utf-8"))
        return GuidelinesManifest.model_validate(raw or {}).bind_source_base(destination.parent)
    except FileNotFoundError:
        raise
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(
            f"Invalid guidelines manifest {destination}: {type(exc).__name__}"
        ) from exc


def save_manifest(path: Path | str, manifest: GuidelinesManifest) -> None:
    """Validate and atomically save a normalized manifest.

    Args:
        path: Destination path for ``guidelines.yml``.
        manifest: Manifest document to persist.

    Raises:
        TypeError: If the document or destination type is unsupported.
        OSError: If atomic publication fails.
        pydantic.ValidationError: If the document is invalid.
    """
    if not isinstance(manifest, GuidelinesManifest):
        raise TypeError("manifest must be a GuidelinesManifest instance")
    if not isinstance(path, (Path, str)):
        raise TypeError("manifest path must be a string or pathlib.Path")
    validated = GuidelinesManifest.model_validate(manifest.model_dump(exclude_none=True))
    _write_yaml(Path(path), validated.model_dump(exclude_none=True))
