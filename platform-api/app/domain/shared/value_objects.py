"""Small immutable values shared across features."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.shared.errors import ValidationFailed

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_RESERVED_SLUGS = frozenset(
    {"admin", "api", "www", "control", "platform", "app", "status", "mail", "support"}
)


@dataclass(frozen=True, slots=True)
class EmailAddress:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if len(normalized) > 254 or not _EMAIL.match(normalized):
            raise ValidationFailed("Email address is not valid.", details={"field": "email"})
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Slug:
    """A URL-safe school handle, used in login links and subdomains."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _SLUG.match(normalized):
            raise ValidationFailed(
                "Slug must be 3-63 characters of lowercase letters, digits and hyphens.",
                details={"field": "slug"},
            )
        if normalized in _RESERVED_SLUGS:
            raise ValidationFailed("This slug is reserved.", details={"field": "slug"})
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_name(cls, name: str) -> Slug:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:63].strip("-")
        return cls(base)


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a keyset-paginated listing."""

    items: list[T]
    next_cursor: str | None
