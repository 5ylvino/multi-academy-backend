"""Announcements: notices from the school to a chosen audience.

The audience is a set of roles (parents, students, class teachers, ...) and, optionally, classes:
"parents of JSS 1A" is roles {parent} and classes {JSS 1A}. Who can see an announcement is worked
out when they read the feed, so people who join a class later still see what is current.

Urgent announcements (emergencies: closures, security alerts) publish at once, are emailed to the
whole audience, and ask each reader to acknowledge them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id

#: Roles an announcement can go to. Kept here (not imported) because domain features are
#: independent; a test checks it matches the school role catalog.
AUDIENCE_ROLES = frozenset(
    {
        "director",
        "school_admin",
        "it_admin",
        "head_teacher",
        "principal",
        "vice_principal",
        "assistant_head_teacher",
        "bursar",
        "class_teacher",
        "subject_teacher",
        "administrative_staff",
        "student",
        "parent",
        "driver",
        "guard",
        "nurse",
    }
)
STAFF_ROLES = AUDIENCE_ROLES - {"student", "parent"}
MAX_CLASSES = 60
MAX_SCHEDULE_AHEAD = timedelta(days=120)
MAX_LIFETIME = timedelta(days=400)


class Priority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"


class AnnouncementState(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class Audience:
    roles: frozenset[str]
    class_ids: frozenset[UUID] = frozenset()

    @classmethod
    def of(cls, roles: list[str], class_ids: list[UUID]) -> Audience:
        clean = frozenset(r.strip().lower() for r in roles if r.strip())
        if not clean:
            raise ValidationFailed("Choose who should see this.", details={"field": "roles"})
        unknown = clean - AUDIENCE_ROLES
        if unknown:
            raise ValidationFailed(
                "Unknown audience.", details={"field": "roles", "unknown": sorted(unknown)}
            )
        if len(set(class_ids)) > MAX_CLASSES:
            raise ValidationFailed(
                f"Pick at most {MAX_CLASSES} classes.", details={"field": "class_ids"}
            )
        return cls(roles=clean, class_ids=frozenset(class_ids))

    def includes(self, roles: frozenset[str], classes: frozenset[UUID]) -> bool:
        if not self.roles & roles:
            return False
        return not self.class_ids or bool(self.class_ids & classes)


@dataclass(frozen=True, kw_only=True)
class UrgentAnnouncementPublished(DomainEvent):
    name: ClassVar[str] = "communication.urgent_announcement"
    announcement_id: UUID
    title: str
    roles: tuple[str, ...]
    class_ids: tuple[str, ...]


def _text(value: str, field: str, low: int, high: int) -> str:
    clean = value.strip()
    if not low <= len(clean) <= high:
        raise ValidationFailed(f"Use {low} to {high} characters.", details={"field": field})
    return clean


@dataclass(kw_only=True, eq=False)
class Announcement(AggregateRoot):
    tenant_id: UUID
    title: str
    body: str
    audience: Audience
    priority: Priority
    pinned: bool
    publish_at: datetime
    expires_at: datetime | None
    author_id: UUID
    created_at: datetime
    edited_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdrawn_by: UUID | None = None
    withdraw_reason: str | None = None

    @classmethod
    def publish(
        cls,
        *,
        tenant_id: UUID,
        title: str,
        body: str,
        audience: Audience,
        priority: Priority,
        pinned: bool,
        publish_at: datetime | None,
        expires_at: datetime | None,
        by: UUID,
        now: datetime,
    ) -> Announcement:
        if priority is Priority.URGENT and publish_at is not None and publish_at > now:
            raise ValidationFailed(
                "Urgent announcements go out at once; they can't be scheduled.",
                details={"field": "publish_at"},
            )
        start = max(publish_at or now, now)
        announcement = cls(
            id=new_id(),
            tenant_id=tenant_id,
            title=_text(title, "title", 3, 160),
            body=_text(body, "body", 1, 10_000),
            audience=audience,
            priority=priority,
            pinned=pinned,
            publish_at=start,
            expires_at=expires_at,
            author_id=by,
            created_at=now,
        )
        announcement._check_window(now)
        if priority is Priority.URGENT:
            announcement.record(
                UrgentAnnouncementPublished(
                    tenant_id=tenant_id,
                    announcement_id=announcement.id,
                    title=announcement.title,
                    roles=tuple(sorted(audience.roles)),
                    class_ids=tuple(sorted(str(c) for c in audience.class_ids)),
                )
            )
        return announcement

    def state(self, now: datetime) -> AnnouncementState:
        if self.withdrawn_at is not None:
            return AnnouncementState.WITHDRAWN
        if self.publish_at > now:
            return AnnouncementState.SCHEDULED
        if self.expires_at is not None and self.expires_at <= now:
            return AnnouncementState.EXPIRED
        return AnnouncementState.LIVE

    def edit(
        self,
        *,
        title: str,
        body: str,
        audience: Audience,
        pinned: bool,
        publish_at: datetime | None,
        expires_at: datetime | None,
        now: datetime,
    ) -> None:
        state = self.state(now)
        if state in {AnnouncementState.WITHDRAWN, AnnouncementState.EXPIRED}:
            raise InvalidStateTransition(
                f"This announcement is {state.value}.", code="announcement_closed"
            )
        if self.priority is Priority.URGENT and audience != self.audience:
            raise ValidationFailed(
                "An urgent announcement has been sent; its audience can't change.",
                details={"field": "roles"},
            )
        self.title = _text(title, "title", 3, 160)
        self.body = _text(body, "body", 1, 10_000)
        self.audience = audience
        self.pinned = pinned
        if state is AnnouncementState.SCHEDULED:
            self.publish_at = max(publish_at or now, now)
        self.expires_at = expires_at
        self._check_window(now)
        if state is AnnouncementState.LIVE:
            self.edited_at = now

    def withdraw(self, *, by: UUID, reason: str, now: datetime) -> None:
        if self.withdrawn_at is not None:
            raise InvalidStateTransition("Already withdrawn.", code="announcement_closed")
        self.withdraw_reason = _text(reason, "reason", 3, 300)
        self.withdrawn_at = now
        self.withdrawn_by = by

    def _check_window(self, now: datetime) -> None:
        if self.publish_at - now > MAX_SCHEDULE_AHEAD:
            raise ValidationFailed(
                "Schedule at most 120 days ahead.", details={"field": "publish_at"}
            )
        if self.expires_at is not None:
            if self.expires_at <= self.publish_at:
                raise ValidationFailed(
                    "The end must be after it goes out.", details={"field": "expires_at"}
                )
            if self.expires_at - self.publish_at > MAX_LIFETIME:
                raise ValidationFailed(
                    "An announcement can stay up for at most 400 days.",
                    details={"field": "expires_at"},
                )
