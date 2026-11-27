"""Domain events: facts that already happened, published through the transactional outbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        """Serializable body for the outbox. Values must be JSON-safe after str() of UUIDs."""
        data: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key in {"event_id", "occurred_at"}:
                continue
            data[key] = str(value) if isinstance(value, UUID) else value
        return data
