"""Single authoritative source-expression validation primitives.

Both :mod:`ai_guidelines.models` and :mod:`ai_guidelines.locations` need to
reject credential-bearing URLs, classify hosts, and validate revisions. Two
independent copies of those rules can silently diverge, which is a correctness
and security risk rather than a tidiness concern. This module therefore owns
the constants and predicates; neither consumer is privileged over the other.

Callers supply their own exception type and message subject so that the public
error vocabulary of each module is preserved.

Examples:
    >>> source_type_for_host("github.com")
    'github'
    >>> validate_revision("main")
    'main'
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

SourceType = Literal["gitlab", "github", "git", "local"]

CREDENTIAL_QUERY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "apikey",
    "auth",
    "signature",
)
"""Substrings whose presence in a query key implies a credential."""

CREDENTIAL_QUERY_KEYS = frozenset({"key", "accesskey", "sig", "expires"})
"""Exact query keys that carry a credential or a signed-URL lifetime."""

REMOTE_SCHEMES = frozenset({"http", "https", "ssh", "git"})
"""URL schemes treated as remote, and therefore credential-checked."""

PATH_METACHARACTERS = frozenset("*?[]!")
"""Glob metacharacters rejected in literal source paths."""

_KEY_ASSIGNMENT = re.compile(r"(?:^|[?&;,])\s*(?P<key>[A-Za-z][A-Za-z0-9_.-]*)\s*=")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def has_control_characters(value: str) -> bool:
    """Return whether a string contains C0 control characters or ``DEL``.

    Args:
        value:
            Candidate string.

    Returns:
        ``True`` when at least one non-printable character is present.
    """
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def is_credential_query_key(key: str) -> bool:
    """Return whether a query key commonly carries a credential.

    Args:
        key:
            Raw query-string key.

    Returns:
        ``True`` when the normalized key is a known credential key or contains
        a credential marker.
    """
    normalized = _NON_ALPHANUMERIC.sub("", key.lower())
    return normalized in CREDENTIAL_QUERY_KEYS or any(
        marker in normalized for marker in CREDENTIAL_QUERY_MARKERS
    )


def validate_fragment_expression(
    fragment: str,
    *,
    error_type: type[ValueError] = ValueError,
    subject: str = "location",
) -> None:
    """Reject credential-like key/value data embedded in a URL fragment.

    Args:
        fragment:
            Raw or percent-encoded fragment.
        error_type:
            Exception type raised on rejection.
        subject:
            Noun used in error messages.

    Raises:
        ValueError: Of ``error_type``, when the fragment is unsafe.

    Examples:
        >>> validate_fragment_expression("section")
    """
    decoded = unquote(fragment)
    if has_control_characters(decoded):
        raise error_type(f"{subject} must contain printable characters")
    if any(
        is_credential_query_key(match.group("key")) for match in _KEY_ASSIGNMENT.finditer(decoded)
    ):
        raise error_type(f"{subject} must not contain credentials")


def validate_remote_expression(
    expression: str,
    *,
    error_type: type[ValueError] = ValueError,
    subject: str = "location",
) -> None:
    """Validate a remote expression without echoing sensitive input.

    Rejects userinfo credentials, credential-like query keys, credential-like
    fragment data, and control characters. ``ssh://git@host`` is allowed
    because ``git`` is a conventional transport user, not a secret.

    Args:
        expression:
            Candidate remote URL or SCP-style expression.
        error_type:
            Exception type raised on rejection.
        subject:
            Noun used in error messages.

    Raises:
        ValueError: Of ``error_type``, when the expression is unsafe or malformed.

    Examples:
        >>> validate_remote_expression("https://example.com/team/repo")
    """
    if has_control_characters(expression):
        raise error_type(f"{subject} must contain printable characters")
    try:
        parsed = urlsplit(expression)
        credentialed = parsed.password is not None or (
            parsed.username is not None
            and not (parsed.scheme.lower() == "ssh" and parsed.username == "git")
        )
        query_keys = [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
        fragment = parsed.fragment
    except ValueError as exc:
        raise error_type(f"{subject} contains an invalid remote URL") from exc
    if credentialed or any(is_credential_query_key(key) for key in query_keys):
        raise error_type(f"{subject} must not contain credentials")
    validate_fragment_expression(fragment, error_type=error_type, subject=subject)


def validate_remote_field(
    value: str,
    *,
    error_type: type[ValueError] = ValueError,
    subject: str = "location",
) -> None:
    """Validate credentials only when a field actually looks remote.

    Args:
        value:
            Candidate field value.
        error_type:
            Exception type raised on rejection.
        subject:
            Noun used in error messages.

    Raises:
        ValueError: Of ``error_type``, when a remote value is unsafe.

    Examples:
        >>> validate_remote_field("https://example.com/team/repo")
    """
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() in REMOTE_SCHEMES or candidate.lower().startswith("git@"):
        validate_remote_expression(candidate, error_type=error_type, subject=subject)


def source_type_for_host(host: str) -> SourceType:
    """Classify a host while retaining a generic Git fallback.

    Args:
        host:
            Hostname, optionally with a port.

    Returns:
        ``"github"``, ``"gitlab"``, or ``"git"``.
    """
    normalized = host.lower().split(":", 1)[0]
    if normalized == "github.com" or normalized.endswith(".github.com"):
        return "github"
    if "gitlab" in normalized:
        return "gitlab"
    return "git"


def validate_revision(value: str, *, error_type: type[ValueError] = ValueError) -> str:
    """Validate one revision before it becomes a Git argument.

    Args:
        value:
            Candidate branch, tag, or commit expression.
        error_type:
            Exception type raised on rejection.

    Returns:
        The decoded, stripped revision.

    Raises:
        ValueError: Of ``error_type``, when the revision is empty, contains
            control characters, or looks like a command-line option.
    """
    if not isinstance(value, str):
        raise error_type("revision must be a printable string")
    decoded = unquote(value)
    if has_control_characters(decoded):
        raise error_type("revision must contain printable characters")
    decoded = decoded.strip()
    if not decoded:
        raise error_type("revision must not be empty")
    if decoded.startswith("-"):
        raise error_type("revision must not begin with an option-like prefix")
    return decoded
