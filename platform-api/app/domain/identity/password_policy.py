"""Password rules (NIST SP 800-63B): length over complexity, block the obvious ones."""

from __future__ import annotations

import re

from app.domain.shared.errors import ValidationFailed

MIN_LENGTH = 10
MAX_LENGTH = 128
_COMMON = frozenset(
    {
        "password123",
        "password1234",
        "1234567890",
        "qwertyuiop",
        "iloveyou12",
        "admin12345",
        "welcome123",
        "letmein123",
        "school1234",
        "abcdefghij",
    }
)

#: Words people pad with digits or symbols, e.g. "Password12!" or "school2026".
_COMMON_BASES = frozenset(
    {
        "password",
        "passw0rd",
        "qwerty",
        "qwertyuiop",
        "letmein",
        "welcome",
        "iloveyou",
        "admin",
        "administrator",
        "school",
        "student",
        "teacher",
        "abc",
        "abcdef",
        "monkey",
        "dragon",
        "football",
        "sunshine",
        "princess",
        "changeme",
    }
)
_PADDING = re.compile(r"^[^a-z]*(?P<base>.*?)[^a-z]*$")


def _is_common(lowered: str) -> bool:
    if lowered in _COMMON or lowered.isdigit():
        return True
    match = _PADDING.match(lowered)
    base = match.group("base") if match else lowered
    return base in _COMMON_BASES


def validate_password(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise ValidationFailed(
            f"Use at least {MIN_LENGTH} characters.", details={"field": "password"}
        )
    if len(password) > MAX_LENGTH:
        raise ValidationFailed(
            f"Use at most {MAX_LENGTH} characters.", details={"field": "password"}
        )
    lowered = password.lower()
    if _is_common(lowered) or len(set(password)) < 4:
        raise ValidationFailed("This password is too easy to guess.", details={"field": "password"})
    if email and email.split("@", 1)[0].lower() in lowered and len(email.split("@", 1)[0]) >= 4:
        raise ValidationFailed(
            "Don't include your email name in the password.", details={"field": "password"}
        )
