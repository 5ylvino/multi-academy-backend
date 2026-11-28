"""Announcements: publish to an audience, the reader's feed, read receipts and urgent alerts."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.communication.announcements import (
    Announcement,
    AnnouncementState,
    Audience,
    Priority,
)
from app.domain.communication.repositories import FeedItem, Reader
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import scope_of
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class AnnouncementView:
    id: UUID
    title: str
    body: str
    roles: list[str]
    class_ids: list[UUID]
    priority: str
    pinned: bool
    publish_at: datetime
    expires_at: datetime | None
    state: str
    author_id: UUID
    author_name: str
    created_at: datetime
    edited_at: datetime | None
    read_at: datetime | None = None
    reads: int | None = None
    withdraw_reason: str | None = None

    @classmethod
    def of(
        cls,
        a: Announcement,
        *,
        now: datetime,
        author: str,
        read_at: datetime | None = None,
        reads: int | None = None,
    ) -> AnnouncementView:
        return cls(
            id=a.id,
            title=a.title,
            body=a.body,
            roles=sorted(a.audience.roles),
            class_ids=sorted(a.audience.class_ids),
            priority=a.priority.value,
            pinned=a.pinned,
            publish_at=a.publish_at,
            expires_at=a.expires_at,
            state=a.state(now).value,
            author_id=a.author_id,
            author_name=author,
            created_at=a.created_at,
            edited_at=a.edited_at,
            read_at=read_at,
            reads=reads,
            withdraw_reason=a.withdraw_reason,
        )


@dataclass(frozen=True, slots=True)
class Feed:
    pinned: list[AnnouncementView]
    items: list[AnnouncementView]
    next_cursor: str | None
    unread: int


@dataclass(frozen=True, slots=True)
class AnnouncementDraft:
    title: str
    body: str
    roles: list[str]
    class_ids: list[UUID]
    pinned: bool = False
    publish_at: datetime | None = None
    expires_at: datetime | None = None
    priority: str = Priority.NORMAL.value


class Announcements:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def publish(self, actor: Principal, draft: AnnouncementDraft) -> AnnouncementView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("announcements:create")
        try:
            priority = Priority(draft.priority)
        except ValueError as exc:
            raise ValidationFailed("Unknown priority.", details={"field": "priority"}) from exc
        if priority is Priority.URGENT:
            actor.require("emergency:send")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        audience = Audience.of(draft.roles, draft.class_ids)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            audience = await self._check_reach(uow, actor, audience)
            announcement = Announcement.publish(
                tenant_id=tenant_id,
                title=draft.title,
                body=draft.body,
                audience=audience,
                priority=priority,
                pinned=draft.pinned,
                publish_at=draft.publish_at,
                expires_at=draft.expires_at,
                by=actor.user_id,
                now=now,
            )
            await uow.communication.add_announcement(announcement)
            uow.track(announcement)
            uow.audit(
                audit.by(
                    actor,
                    "communication.announcement_published",
                    target_type="announcement",
                    target_id=announcement.id,
                    title=announcement.title,
                    priority=priority.value,
                    roles=sorted(audience.roles),
                    classes=len(audience.class_ids),
                    publish_at=announcement.publish_at.isoformat(),
                )
            )
            names = await uow.users.school_names({actor.user_id})
            await uow.commit()
        return AnnouncementView.of(announcement, now=now, author=names.get(actor.user_id, ""))

    async def edit(
        self, actor: Principal, announcement_id: UUID, draft: AnnouncementDraft
    ) -> AnnouncementView:
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        audience = Audience.of(draft.roles, draft.class_ids)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            a = await self._get(uow, announcement_id, for_update=True)
            if a.author_id != actor.user_id:
                actor.require("announcements:update")
            if audience != a.audience:
                audience = await self._check_reach(uow, actor, audience)
            before = _fields(a)
            a.edit(
                title=draft.title,
                body=draft.body,
                audience=audience,
                pinned=draft.pinned,
                publish_at=draft.publish_at,
                expires_at=draft.expires_at,
                now=now,
            )
            await uow.communication.save_announcement(a)
            uow.audit(
                audit.by(
                    actor,
                    "communication.announcement_edited",
                    target_type="announcement",
                    target_id=a.id,
                    changes=audit.changes(before, _fields(a)),
                )
            )
            names = await uow.users.school_names({a.author_id})
            await uow.commit()
        return AnnouncementView.of(a, now=now, author=names.get(a.author_id, ""))

    async def withdraw(
        self, actor: Principal, announcement_id: UUID, *, reason: str
    ) -> AnnouncementView:
        """Take it down. It stays on record (with the reason) for the activity log."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            a = await self._get(uow, announcement_id, for_update=True)
            if a.author_id != actor.user_id:
                actor.require("announcements:delete")
            a.withdraw(by=actor.user_id, reason=reason, now=now)
            await uow.communication.save_announcement(a)
            uow.audit(
                audit.by(
                    actor,
                    "communication.announcement_withdrawn",
                    target_type="announcement",
                    target_id=a.id,
                    title=a.title,
                    reason=a.withdraw_reason,
                )
            )
            names = await uow.users.school_names({a.author_id})
            await uow.commit()
        return AnnouncementView.of(a, now=now, author=names.get(a.author_id, ""))

    async def feed(self, actor: Principal, *, cursor: str | None, limit: int | None) -> Feed:
        """What the signed-in person should see: pinned first, then newest first."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            reader = await reader_of(uow, actor)
            pinned = (
                await uow.communication.feed(reader, now=now, pinned=True, cursor=None, limit=20)
                if cursor is None
                else None
            )
            page = await uow.communication.feed(
                reader, now=now, pinned=False, cursor=cursor, limit=clamp_limit(limit)
            )
            unread = await uow.communication.unread_announcements(reader, now=now)
            items = (pinned.items if pinned else []) + page.items
            names = await uow.users.school_names({i.announcement.author_id for i in items})

        def view(item: FeedItem) -> AnnouncementView:
            a = item.announcement
            return AnnouncementView.of(
                a, now=now, author=names.get(a.author_id, ""), read_at=item.read_at
            )

        return Feed(
            pinned=[view(i) for i in (pinned.items if pinned else [])],
            items=[view(i) for i in page.items],
            next_cursor=page.next_cursor,
            unread=unread,
        )

    async def open(self, actor: Principal, announcement_id: UUID) -> AnnouncementView:
        """Read one announcement and record that it was read (for urgent ones, the
        acknowledgement)."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            a = await self._get(uow, announcement_id)
            reader = await reader_of(uow, actor)
            visible = a.state(now) is AnnouncementState.LIVE and a.audience.includes(
                reader.roles, reader.class_ids
            )
            if not visible and not self._manages(actor, a):
                raise NotFound("Announcement not found.")
            read_at = None
            if visible:
                await uow.communication.mark_announcement_read(a.id, actor.user_id, now=now)
                read_at = now
            names = await uow.users.school_names({a.author_id})
            await uow.commit()  # audit: not needed. A read receipt, kept in its own table.
        return AnnouncementView.of(a, now=now, author=names.get(a.author_id, ""), read_at=read_at)

    async def managed(
        self, actor: Principal, *, state: str | None, cursor: str | None, limit: int | None
    ) -> Page[AnnouncementView]:
        """Everything published, with read counts, for the people who publish."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("announcements:create")
        if state is not None and state not in {s.value for s in AnnouncementState}:
            raise ValidationFailed("Unknown state.", details={"field": "state"})
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.communication.managed(
                state=state, now=now, cursor=cursor, limit=clamp_limit(limit)
            )
            names = await uow.users.school_names({m.announcement.author_id for m in page.items})
        return Page(
            items=[
                AnnouncementView.of(
                    m.announcement,
                    now=now,
                    author=names.get(m.announcement.author_id, ""),
                    reads=m.reads,
                )
                for m in page.items
            ],
            next_cursor=page.next_cursor,
        )

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _manages(actor: Principal, a: Announcement) -> bool:
        return a.author_id == actor.user_id or actor.permissions.allows("announcements:update")

    @staticmethod
    async def _check_reach(uow: UnitOfWork, actor: Principal, audience: Audience) -> Audience:
        """Whole-school leaders can announce to anyone. A section's heads reach their own
        section (no classes picked means every class in it). Teachers reach the classes they
        teach."""
        classes = await uow.academics.list_classes(level=None, include_inactive=True)
        if audience.class_ids and not audience.class_ids <= {c.id for c in classes}:
            raise ValidationFailed("Unknown class.", details={"field": "class_ids"})
        if actor.permissions.allows("announcements:school_wide"):
            scope = await scope_of(uow, actor)
            if scope.whole_school:
                return audience
            mine = {c.id for c in classes if scope.covers(c.school_level)}
            if audience.class_ids:
                if not audience.class_ids <= mine:
                    raise PermissionDenied(
                        "Some of those classes are in another section.", code="other_section"
                    )
                return audience
            active = {c.id for c in classes if c.id in mine and c.active}
            if not active:
                raise ValidationFailed(
                    "Your section has no classes yet.", details={"field": "class_ids"}
                )
            return Audience(roles=audience.roles, class_ids=frozenset(active))
        taught = await uow.academics.classes_taught_by(actor.user_id)
        if not audience.class_ids or not audience.class_ids <= taught:
            raise PermissionDenied(
                "You can announce to the classes you teach.", code="outside_your_classes"
            )
        return audience

    @staticmethod
    async def _get(
        uow: UnitOfWork, announcement_id: UUID, *, for_update: bool = False
    ) -> Announcement:
        a = await uow.communication.get_announcement(announcement_id, for_update=for_update)
        if a is None:
            raise NotFound("Announcement not found.")
        return a


async def reader_of(uow: UnitOfWork, actor: Principal) -> Reader:
    """The person's roles and every class they belong to this session."""
    roles = frozenset(actor.roles)
    classes: builtins.set[UUID] = set()
    term = await uow.academics.current_term()
    if term is not None:
        students: builtins.set[UUID] = set()
        if SchoolRole.STUDENT.value in roles:
            students.add(actor.user_id)
        if SchoolRole.PARENT.value in roles:
            students |= {link.student_id for link in await uow.guardians.children_of(actor.user_id)}
        classes |= await uow.academics.classes_of_students(students, term.session_id)
    if roles - {SchoolRole.STUDENT.value, SchoolRole.PARENT.value}:
        classes |= await uow.academics.classes_taught_by(actor.user_id)
        scope = await scope_of(uow, actor)
        if not scope.whole_school and scope.levels:
            # Section staff read what is sent to any class of their section.
            classes |= {
                c.id
                for c in await uow.academics.list_classes(level=None, include_inactive=False)
                if scope.covers(c.school_level)
            }
    return Reader(user_id=actor.user_id, roles=roles, class_ids=frozenset(classes))


def _fields(a: Announcement) -> dict[str, object]:
    return {
        "title": a.title,
        "body": a.body,
        "roles": sorted(a.audience.roles),
        "classes": sorted(str(c) for c in a.audience.class_ids),
        "pinned": a.pinned,
        "publish_at": a.publish_at.isoformat(),
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }
