"""Parse the numeric semantic-version grammar used by remote Git resolution.

The grammar accepts exact three-component versions, wildcard forms such as
``1.*`` and ``1.2.*``, caret and tilde bounds, and whitespace- or
comma-separated comparison terms.  Tags may include a leading ``v``; prerelease
identifiers and hyphen ranges are intentionally unsupported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION = re.compile(
    r"^(?P<operator>>=|<=|>|<|\^|~|=)?v?"
    r"(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*|\*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*|\*))?$"
)
_SEMVER_MARKERS = (">", "<", "=", "~", "^")


@dataclass(frozen=True)
class VersionTerm:
    """One parsed semantic-version comparison or wildcard term."""

    operator: str
    version: tuple[int, int, int]
    components: int
    wildcard: bool


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Parse a numeric tag into a normalized three-component version tuple.

    Args:
        value:
            Tag text with an optional leading ``v``.

    Returns:
        A normalized ``(major, minor, patch)`` tuple, or ``None`` for ranges,
        wildcards, and unsupported tag syntax.
    """
    match = _VERSION.fullmatch(value.strip())
    if match is None or match.group("operator") is not None:
        return None
    if "*" in value:
        return None
    return _version_from_match(match)


def parse_range(value: str) -> tuple[VersionTerm, ...]:
    """Parse comparison, caret, tilde, wildcard, or exact range syntax.

    Args:
        value:
            A whitespace- or comma-separated semantic-version expression.

    Returns:
        Parsed terms in evaluation order.

    Raises:
        ValueError:
            If the expression is empty, malformed, or combines an operator
            with a wildcard.

    Examples:
        >>> parse_range(">=1.2.0 <2.0.0")[0].operator
        '>='
        >>> satisfies((1, 4, 2), "^1.2.0")
        True
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("semantic-version range must not be empty")
    terms: list[VersionTerm] = []
    for raw_term in re.split(r"[\s,]+", value.strip()):
        if not raw_term:
            continue
        match = _VERSION.fullmatch(raw_term)
        if match is None:
            raise ValueError("invalid semantic-version range")
        minor = match.group("minor")
        patch = match.group("patch")
        wildcard = minor == "*" or patch == "*"
        if wildcard and match.group("operator") is not None:
            raise ValueError("semantic-version wildcards cannot use comparisons")
        if patch == "*" and minor is None:
            raise ValueError("semantic-version patch wildcard requires a minor component")
        components = 1 if minor in (None, "*") else 2 if patch in (None, "*") else 3
        if not wildcard and match.group("operator") is None and components < 3:
            wildcard = True
        terms.append(
            VersionTerm(
                operator=match.group("operator") or "=",
                version=_version_from_match(match),
                components=components,
                wildcard=wildcard,
            )
        )
    if not terms:
        raise ValueError("semantic-version range must not be empty")
    return tuple(terms)


def is_range(value: str | None) -> bool:
    """Return whether a revision is intended to be a semantic-version range.

    Args:
        value:
            Optional revision expression.

    Returns:
        ``True`` for operators, wildcards, partial versions, or multiple
        comparison terms; exact three-component tags remain exact references.
    """
    if value is None or not value.strip():
        return False
    candidate = value.strip()
    marked = any(candidate.startswith(marker) for marker in _SEMVER_MARKERS)
    wildcard = "*" in candidate
    multiple_terms = len([term for term in re.split(r"[\s,]+", candidate) if term]) > 1
    partial = re.fullmatch(r"v?(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))?", candidate) is not None
    if not marked and not wildcard and not multiple_terms and not partial:
        return False
    try:
        parse_range(candidate)
    except ValueError:
        return True
    return marked or wildcard or multiple_terms or partial


def satisfies(version: tuple[int, int, int], expression: str) -> bool:
    """Return whether a version satisfies every term in a range.

    Args:
        version:
            Three-component version tuple to test.
        expression:
            Supported semantic-version range expression.

    Returns:
        ``True`` when every parsed comparison term matches ``version``.

    Raises:
        ValueError:
            If ``expression`` is not valid semantic-version syntax.
    """
    return all(_matches_term(version, term) for term in parse_range(expression))


def _version_from_match(match: re.Match[str]) -> tuple[int, int, int]:
    """Convert one validated version match to a tuple."""
    minor = match.group("minor")
    patch = match.group("patch")
    return (
        int(match.group("major")),
        0 if minor in (None, "*") else int(minor),
        0 if patch in (None, "*") else int(patch),
    )


def _matches_term(version: tuple[int, int, int], term: VersionTerm) -> bool:
    """Evaluate one parsed term against a version."""
    if term.wildcard:
        return version[: term.components] == term.version[: term.components]
    target = term.version
    if term.operator == "^":
        if target[0] > 0:
            upper = (target[0] + 1, 0, 0)
        elif target[1] > 0:
            upper = (target[0], target[1] + 1, 0)
        else:
            upper = (target[0], target[1], target[2] + 1)
        return target <= version < upper
    if term.operator == "~":
        upper = (target[0] + 1, 0, 0) if term.components == 1 else (target[0], target[1] + 1, 0)
        return target <= version < upper
    return {
        ">=": version >= target,
        "<=": version <= target,
        ">": version > target,
        "<": version < target,
        "=": version == target,
    }[term.operator]
