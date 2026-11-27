"""Base classes for entities and aggregate roots."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from masms.domain.shared.events import DomainEvent


@dataclass(kw_only=True, eq=False)
class Entity:
    """An object defined by its identity, not its attributes."""

    id: UUID

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and other.id == self.id

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))


@dataclass(kw_only=True, eq=False)
class AggregateRoot(Entity):
    """Consistency boundary. Records events that the unit of work writes to the outbox."""

    _events: list[DomainEvent] = field(default_factory=list, init=False, repr=False)

    def record(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        events, self._events = self._events, []
        return events
