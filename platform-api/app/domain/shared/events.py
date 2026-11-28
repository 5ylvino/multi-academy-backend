"""Domain events: facts that already happened, published through the transactional outbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from uuid import UUID

from app.domain.shared.ids import new_id


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Base event. Subclasses set ``name`` and add their own fields."""

    name: ClassVar[str] = "domain.event"
    sensitive_fields: ClassVar[frozenset[str]] = frozenset()

    event_id: UUID = field(default_factory=new_id)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tenant_id: UUID | None = None

    def payload(self) -> dict[str, Any]:
        """Serializable body for the outbox. UUIDs become strings and dates ISO 8601 strings;
        every other value must already be JSON-safe."""
        data: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key in {"event_id", "occurred_at"}:
                continue
            if isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, date):  # datetime is a date too
                value = value.isoformat()
            data[key] = value
        return data
