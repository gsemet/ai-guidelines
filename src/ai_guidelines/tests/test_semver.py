"""Tests for the shared semantic-version parser and evaluator."""

from __future__ import annotations

import pytest

from ai_guidelines.semver import is_range, parse_range, parse_version, satisfies


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("1.2", (1, 2, 0)),
        ("1.2.*", None),
        ("^1.2.3", None),
        ("1.2.3-alpha", None),
    ],
)
def test_parse_version_normalizes_numeric_tags_and_rejects_ranges(
    value: str, expected: tuple[int, int, int] | None
) -> None:
    """Numeric tags normalize while range and prerelease syntax do not."""
    assert parse_version(value) == expected


@pytest.mark.parametrize(
    ("expression", "version", "expected"),
    [
        ("^1.2.3", (1, 2, 3), True),
        ("^1.2.3", (1, 9, 0), True),
        ("^1.2.3", (2, 0, 0), False),
        ("~1.2.3", (1, 2, 9), True),
        ("~1.2.3", (1, 3, 0), False),
        (">=1.2.0 <2.0.0", (1, 8, 4), True),
        (">=1.2.0 <2.0.0", (2, 0, 0), False),
        (">1.2.0", (1, 2, 1), True),
        (">1.2.0", (1, 2, 0), False),
        ("<=1.2.0", (1, 2, 0), True),
        ("<=1.2.0", (1, 2, 1), False),
        ("1.2.3", (1, 2, 3), True),
        ("1.2.3", (1, 2, 4), False),
        ("1.*", (1, 99, 0), True),
        ("1.*", (2, 0, 0), False),
        ("1.2.*", (1, 2, 99), True),
        ("1.2.*", (1, 3, 0), False),
        (">=1.0.0, <2.0.0", (1, 5, 0), True),
    ],
)
def test_satisfies_supported_range_forms(
    expression: str,
    version: tuple[int, int, int],
    expected: bool,
) -> None:
    """Evaluate exact, wildcard, bound, caret, and tilde expressions."""
    assert satisfies(version, expression) is expected


@pytest.mark.parametrize("value", ["", "1..2.3", ">=1.*", "^1.2.*", "1.2.3.4"])
def test_parse_range_rejects_invalid_forms(value: str) -> None:
    """Malformed or operator-plus-wildcard expressions fail clearly."""
    with pytest.raises(ValueError):
        parse_range(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.3", False),
        ("v1.2.3", False),
        ("^1.2.3", True),
        ("~1.2.3", True),
        (">=1.0.0 <2.0.0", True),
        ("1.*", True),
        ("1.2", True),
    ],
)
def test_is_range_distinguishes_exact_tags_from_ranges(value: str, expected: bool) -> None:
    """Revision classification preserves exact tags and detects moving forms."""
    assert is_range(value) is expected
