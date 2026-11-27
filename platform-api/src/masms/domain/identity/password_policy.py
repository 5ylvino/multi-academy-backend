"""Password rules (NIST SP 800-63B): length over complexity, block the obvious ones."""

from __future__ import annotations

from masms.domain.shared.errors import ValidationFailed

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
    if lowered in _COMMON or len(set(password)) < 4:
        raise ValidationFailed("This password is too easy to guess.", details={"field": "password"})
    if email and email.split("@", 1)[0].lower() in lowered and len(email.split("@", 1)[0]) >= 4:
        raise ValidationFailed(
            "Don't include your email name in the password.", details={"field": "password"}
        )
