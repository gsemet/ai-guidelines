"""Expand manifest selectors into safe sparse-checkout patterns."""

from __future__ import annotations

from pathlib import PurePosixPath

from ai_guidelines.models import GuidelineDeclaration


def _validate_selector(selector: str) -> str:
    """Return a normalized plural selector or raise a safe validation error."""
    normalized = selector.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("selector must be a printable relative path")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("selector must be a safe relative path")
    if PurePosixPath(normalized).is_absolute():
        raise ValueError("selector must be a relative path")
    if any(part.startswith("-") for part in parts):
        raise ValueError("selector components must not begin with an option-like prefix")
    return "/".join(parts)


def selector_sparse_patterns(declaration: GuidelineDeclaration) -> list[str]:
    """Return deterministic sparse patterns for one declaration.

    Plural selectors intentionally include both the literal selector and its
    directory/file variants. The literal entry allows a selector that names a
    file, while the other patterns support the suffixless folder convention.
    Legacy ``pattern`` declarations retain their recursive filename matching
    behavior.
    """
    if declaration.paths is not None:
        patterns: list[str] = []
        for raw_selector in declaration.paths:
            selector = _validate_selector(raw_selector)
            patterns.extend(
                [
                    selector,
                    f"{selector}/*",
                    f"{selector}.guideline.md",
                    f"{selector}.guidelines.md",
                ]
            )
        return list(dict.fromkeys(patterns))
    if declaration.pattern is not None:
        return [
            f"**/{declaration.pattern}.guideline.md",
            f"**/{declaration.pattern}.guidelines.md",
        ]
    if declaration.path is not None:
        path = declaration.path.replace("\\", "/").rstrip("/")
        if path.endswith((".guideline.md", ".guidelines.md")):
            return [path]
        return [
            path,
            f"{path}/*",
            f"{path}.guideline.md",
            f"{path}.guidelines.md",
        ]
    return []


expand_selector_patterns = selector_sparse_patterns
build_sparse_patterns = selector_sparse_patterns
