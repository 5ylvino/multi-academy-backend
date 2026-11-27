"""Announcements, conversations and the notification inbox."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, literal, or_, select, tuple_, union, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.communication.announcements import Announcement, Audience, Priority
from app.domain.communication.conversations import Conversation, Message
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.communication.repositories import (
    Contact,
    ConversationSummary,
    FeedItem,
    ManagedAnnouncement,
    Reader,
)
from app.domain.shared.errors import ValidationFailed
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.academics import (
    ClassSubjectRow,
    EnrolmentRow,
    SchoolClassRow,
)
from app.infrastructure.persistence.models.communication import (
    AnnouncementReadRow,
    AnnouncementRow,
    ConversationRow,
    MessageRow,
    NotificationRow,
    ParticipantRow,
)
from app.infrastructure.persistence.models.identity import GuardianLinkRow, SchoolUserRow
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _encode_at(at: datetime, row_id: UUID) -> str:
    raw = f"{at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_at(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, row_id = raw.partition("|")
        return datetime.fromisoformat(at), UUID(row_id)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValidationFailed("Cursor is not valid.", details={"field": "cursor"}) from exc


def _announcement(r: AnnouncementRow) -> Announcement:
    return Announcement(
        id=r.id,
        tenant_id=r.tenant_id,
        title=r.title,
        body=r.body,
        audience=Audience(roles=frozenset(r.audience_roles), class_ids=frozenset(r.class_ids)),
        priority=Priority(r.priority),
        pinned=r.pinned,
        publish_at=r.publish_at,
        expires_at=r.expires_at,
        author_id=r.author_id,
        created_at=r.created_at,
        edited_at=r.edited_at,
        withdrawn_at=r.withdrawn_at,
        withdrawn_by=r.withdrawn_by,
        withdraw_reason=r.withdraw_reason,
    )


def _announcement_values(a: Announcement) -> dict[str, Any]:
    return {
        "title": a.title,
        "body": a.body,
        "audience_roles": sorted(a.audience.roles),
        "class_ids": sorted(a.audience.class_ids),
        "priority": a.priority.value,
        "pinned": a.pinned,
        "publish_at": a.publish_at,
        "expires_at": a.expires_at,
        "edited_at": a.edited_at,
        "withdrawn_at": a.withdrawn_at,
        "withdrawn_by": a.withdrawn_by,
        "withdraw_reason": a.withdraw_reason,
    }


def _message(r: MessageRow) -> Message:
    return Message(
        id=r.id,
        tenant_id=r.tenant_id,
        conversation_id=r.conversation_id,
        sender_id=r.sender_id,
        body=r.body,
        sent_at=r.sent_at,
    )


def _notification(r: NotificationRow) -> Notification:
    return Notification(
        id=r.id,
        tenant_id=r.tenant_id,
        user_id=r.user_id,
        kind=NotificationKind(r.kind),
        title=r.title,
        body=r.body,
        link_type=r.link_type,
        link_id=r.link_id,
        created_at=r.created_at,
        read_at=r.read_at,
    )


class SqlCommunicationRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Communication needs a tenant scope.")
        return self._scope.tenant_id

    # ---- announcements ----------------------------------------------------------------------

    async def add_announcement(self, announcement: Announcement) -> None:
        a = announcement
        await self._s.execute(
            pg_insert(AnnouncementRow).values(
                tenant_id=self._tid,
                id=a.id,
                author_id=a.author_id,
                created_at=a.created_at,
                **_announcement_values(a),
            )
        )

    async def get_announcement(
        self, announcement_id: UUID, *, for_update: bool = False
    ) -> Announcement | None:
        stmt = select(AnnouncementRow).where(
            AnnouncementRow.tenant_id == self._tid, AnnouncementRow.id == announcement_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _announcement(row) if row else None

    async def save_announcement(self, announcement: Announcement) -> None:
        await self._s.execute(
            update(AnnouncementRow)
            .where(AnnouncementRow.tenant_id == self._tid, AnnouncementRow.id == announcement.id)
            .values(**_announcement_values(announcement))
        )

    def _visible(self, reader: Reader, now: datetime) -> list[ColumnElement[bool]]:
        a = AnnouncementRow
        in_class = func.cardinality(a.class_ids) == 0
        if reader.class_ids:
            in_class = or_(in_class, a.class_ids.overlap(sorted(reader.class_ids)))
        return [
            a.tenant_id == self._tid,
            a.withdrawn_at.is_(None),
            a.publish_at <= now,
            or_(a.expires_at.is_(None), a.expires_at > now),
            a.audience_roles.overlap(sorted(reader.roles)),
            in_class,
        ]

    async def feed(
        self, reader: Reader, *, now: datetime, pinned: bool, cursor: str | None, limit: int
    ) -> Page[FeedItem]:
        a, r = AnnouncementRow, AnnouncementReadRow
        stmt = (
            select(a, r.read_at)
            .outerjoin(
                r,
                and_(
                    r.tenant_id == a.tenant_id,
                    r.announcement_id == a.id,
                    r.user_id == reader.user_id,
                ),
            )
            .where(*self._visible(reader, now), a.pinned.is_(pinned))
        )
        if cursor:
            at, row_id = _decode_at(cursor)
            stmt = stmt.where(tuple_(a.publish_at, a.id) < tuple_(literal(at), literal(row_id)))
        rows = list(
            (
                await self._s.execute(
                    stmt.order_by(a.publish_at.desc(), a.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[FeedItem(announcement=_announcement(row), read_at=read) for row, read in rows],
            next_cursor=_encode_at(rows[-1][0].publish_at, rows[-1][0].id)
            if more and rows
            else None,
        )

    async def unread_announcements(self, reader: Reader, *, now: datetime) -> int:
        a, r = AnnouncementRow, AnnouncementReadRow
        read = (
            select(r.announcement_id)
            .where(
                r.tenant_id == a.tenant_id,
                r.announcement_id == a.id,
                r.user_id == reader.user_id,
            )
            .exists()
        )
        count = await self._s.scalar(
            select(func.count()).select_from(a).where(*self._visible(reader, now), ~read)
        )
        return int(count or 0)

    async def mark_announcement_read(
        self, announcement_id: UUID, user_id: UUID, *, now: datetime
    ) -> bool:
        result = await self._s.execute(
            pg_insert(AnnouncementReadRow)
            .values(
                tenant_id=self._tid, announcement_id=announcement_id, user_id=user_id, read_at=now
            )
            .on_conflict_do_nothing()
            .returning(AnnouncementReadRow.user_id)
        )
        return result.first() is not None

    async def managed(
        self, *, state: str | None, now: datetime, cursor: str | None, limit: int
    ) -> Page[ManagedAnnouncement]:
        a, r = AnnouncementRow, AnnouncementReadRow
        reads = (
            select(func.count())
            .where(r.tenant_id == a.tenant_id, r.announcement_id == a.id)
            .scalar_subquery()
        )
        stmt = select(a, reads).where(a.tenant_id == self._tid)
        if state == "withdrawn":
            stmt = stmt.where(a.withdrawn_at.is_not(None))
        elif state == "scheduled":
            stmt = stmt.where(a.withdrawn_at.is_(None), a.publish_at > now)
        elif state == "expired":
            stmt = stmt.where(a.withdrawn_at.is_(None), a.expires_at <= now)
        elif state == "live":
            stmt = stmt.where(
                a.withdrawn_at.is_(None),
                a.publish_at <= now,
                or_(a.expires_at.is_(None), a.expires_at > now),
            )
        if cursor:
            stmt = stmt.where(a.id < _decode(cursor))
        rows = list((await self._s.execute(stmt.order_by(a.id.desc()).limit(limit + 1))).all())
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[
                ManagedAnnouncement(announcement=_announcement(row), reads=int(count))
                for row, count in rows
            ],
            next_cursor=_encode(rows[-1][0].id) if more and rows else None,
        )

    async def audience_contacts(
        self, audience: Audience, *, session_id: UUID | None
    ) -> list[Contact]:
        u = SchoolUserRow
        stmt = select(u.id, u.email, u.full_name).where(
            u.tenant_id == self._tid,
            u.status == "active",
            u.roles.overlap(sorted(audience.roles)),
        )
        if audience.class_ids:
            classes = sorted(audience.class_ids)
            students = select(EnrolmentRow.student_id).where(
                EnrolmentRow.tenant_id == self._tid,
                EnrolmentRow.class_id.in_(classes),
                EnrolmentRow.status == "active",
                EnrolmentRow.session_id == session_id if session_id is not None else literal(False),
            )
            parents = select(GuardianLinkRow.parent_id).where(
                GuardianLinkRow.tenant_id == self._tid,
                GuardianLinkRow.student_id.in_(students),
            )
            homeroom = select(SchoolClassRow.class_teacher_id).where(
                SchoolClassRow.tenant_id == self._tid,
                SchoolClassRow.id.in_(classes),
                SchoolClassRow.class_teacher_id.is_not(None),
            )
            subject = select(ClassSubjectRow.teacher_id).where(
                ClassSubjectRow.tenant_id == self._tid,
                ClassSubjectRow.class_id.in_(classes),
                ClassSubjectRow.teacher_id.is_not(None),
            )
            people = union(students, parents, homeroom, subject).subquery()
            stmt = stmt.where(u.id.in_(select(people.c[0])))
        rows = await self._s.execute(stmt.order_by(u.id))
        return [Contact(user_id=i, email=str(e), full_name=n) for i, e, n in rows.all()]

    # ---- conversations ----------------------------------------------------------------------

    async def add_conversation(self, conversation: Conversation) -> None:
        c = conversation
        await self._s.execute(
            pg_insert(ConversationRow).values(
                tenant_id=self._tid,
                id=c.id,
                subject=c.subject,
                student_id=c.student_id,
                created_by=c.created_by,
                created_at=c.created_at,
                last_message_at=c.last_message_at,
                message_count=c.message_count,
            )
        )
        await self._s.execute(
            pg_insert(ParticipantRow).values(
                [
                    {
                        "tenant_id": self._tid,
                        "conversation_id": c.id,
                        "user_id": user_id,
                        "last_read_at": c.created_at if user_id == c.created_by else None,
                    }
                    for user_id in sorted(c.participants)
                ]
            )
        )
        await self._append(c)

    async def get_conversation(
        self, conversation_id: UUID, *, for_update: bool = False
    ) -> Conversation | None:
        stmt = select(ConversationRow).where(
            ConversationRow.tenant_id == self._tid, ConversationRow.id == conversation_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        if row is None:
            return None
        people = await self._participants([row.id])
        return self._conversation(row, people.get(row.id, frozenset()))

    async def save_conversation(self, conversation: Conversation) -> None:
        c = conversation
        await self._s.execute(
            update(ConversationRow)
            .where(ConversationRow.tenant_id == self._tid, ConversationRow.id == c.id)
            .values(last_message_at=c.last_message_at, message_count=c.message_count)
        )
        await self._append(c)

    async def messages(
        self, conversation_id: UUID, *, before: str | None, limit: int
    ) -> Page[Message]:
        stmt = select(MessageRow).where(
            MessageRow.tenant_id == self._tid, MessageRow.conversation_id == conversation_id
        )
        if before:
            stmt = stmt.where(MessageRow.id < _decode(before))
        rows = list(await self._s.scalars(stmt.order_by(MessageRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_message(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def conversations_of(
        self, user_id: UUID, *, cursor: str | None, limit: int
    ) -> Page[ConversationSummary]:
        c, p, m = ConversationRow, ParticipantRow, MessageRow
        unread = (
            select(func.count())
            .where(
                m.tenant_id == c.tenant_id,
                m.conversation_id == c.id,
                m.sender_id != user_id,
                or_(p.last_read_at.is_(None), m.sent_at > p.last_read_at),
            )
            .scalar_subquery()
        )
        stmt = (
            select(c, unread)
            .join(p, and_(p.tenant_id == c.tenant_id, p.conversation_id == c.id))
            .where(c.tenant_id == self._tid, p.user_id == user_id)
        )
        if cursor:
            at, row_id = _decode_at(cursor)
            stmt = stmt.where(
                tuple_(c.last_message_at, c.id) < tuple_(literal(at), literal(row_id))
            )
        rows = list(
            (
                await self._s.execute(
                    stmt.order_by(c.last_message_at.desc(), c.id.desc()).limit(limit + 1)
                )
            ).all()
        )
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [row.id for row, _ in rows]
        people = await self._participants(ids)
        last: dict[UUID, Message] = {}
        if ids:
            latest = await self._s.scalars(
                select(m)
                .where(m.tenant_id == self._tid, m.conversation_id.in_(ids))
                .ext(distinct_on(m.conversation_id))
                .order_by(m.conversation_id, m.id.desc())
            )
            last = {r.conversation_id: _message(r) for r in latest}
        return Page(
            items=[
                ConversationSummary(
                    conversation=self._conversation(row, people.get(row.id, frozenset())),
                    unread=int(count),
                    last_message=last.get(row.id),
                )
                for row, count in rows
            ],
            next_cursor=(
                _encode_at(rows[-1][0].last_message_at, rows[-1][0].id) if more and rows else None
            ),
        )

    async def mark_conversation_read(
        self, conversation_id: UUID, user_id: UUID, *, now: datetime
    ) -> None:
        await self._s.execute(
            update(ParticipantRow)
            .where(
                ParticipantRow.tenant_id == self._tid,
                ParticipantRow.conversation_id == conversation_id,
                ParticipantRow.user_id == user_id,
            )
            .values(last_read_at=now)
        )

    async def _participants(self, ids: list[UUID]) -> dict[UUID, frozenset[UUID]]:
        if not ids:
            return {}
        rows = await self._s.execute(
            select(ParticipantRow.conversation_id, ParticipantRow.user_id).where(
                ParticipantRow.tenant_id == self._tid, ParticipantRow.conversation_id.in_(ids)
            )
        )
        found: dict[UUID, set[UUID]] = {}
        for conversation_id, user_id in rows.all():
            found.setdefault(conversation_id, set()).add(user_id)
        return {k: frozenset(v) for k, v in found.items()}

    @staticmethod
    def _conversation(row: ConversationRow, people: frozenset[UUID]) -> Conversation:
        return Conversation(
            id=row.id,
            tenant_id=row.tenant_id,
            subject=row.subject,
            student_id=row.student_id,
            created_by=row.created_by,
            created_at=row.created_at,
            last_message_at=row.last_message_at,
            participants=people,
            message_count=row.message_count,
        )

    async def _append(self, conversation: Conversation) -> None:
        if not conversation.new_messages:
            return
        await self._s.execute(
            pg_insert(MessageRow).values(
                [
                    {
                        "tenant_id": self._tid,
                        "id": m.id,
                        "conversation_id": m.conversation_id,
                        "sender_id": m.sender_id,
                        "body": m.body,
                        "sent_at": m.sent_at,
                    }
                    for m in conversation.new_messages
                ]
            )
        )
        conversation.new_messages.clear()

    # ---- inbox ------------------------------------------------------------------------------

    async def notify(self, notifications: list[Notification]) -> None:
        for n in notifications:
            values = {
                "tenant_id": self._tid,
                "id": n.id,
                "user_id": n.user_id,
                "kind": n.kind.value,
                "title": n.title,
                "body": n.body,
                "link_type": n.link_type,
                "link_id": n.link_id,
                "created_at": n.created_at,
            }
            stmt = pg_insert(NotificationRow).values(**values)
            if n.kind is NotificationKind.MESSAGE:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        NotificationRow.tenant_id,
                        NotificationRow.user_id,
                        NotificationRow.link_id,
                    ],
                    index_where=and_(
                        NotificationRow.kind == NotificationKind.MESSAGE.value,
                        NotificationRow.read_at.is_(None),
                    ),
                    set_={"title": n.title, "body": n.body, "created_at": n.created_at},
                )
            await self._s.execute(stmt)

    async def notifications(
        self, user_id: UUID, *, unread_only: bool, cursor: str | None, limit: int
    ) -> Page[Notification]:
        n = NotificationRow
        stmt = select(n).where(n.tenant_id == self._tid, n.user_id == user_id)
        if unread_only:
            stmt = stmt.where(n.read_at.is_(None))
        if cursor:
            at, row_id = _decode_at(cursor)
            stmt = stmt.where(tuple_(n.created_at, n.id) < tuple_(literal(at), literal(row_id)))
        rows = list(
            await self._s.scalars(stmt.order_by(n.created_at.desc(), n.id.desc()).limit(limit + 1))
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_notification(r) for r in rows],
            next_cursor=_encode_at(rows[-1].created_at, rows[-1].id) if more and rows else None,
        )

    async def unread_notifications(self, user_id: UUID) -> int:
        n = NotificationRow
        count = await self._s.scalar(
            select(func.count()).where(
                n.tenant_id == self._tid, n.user_id == user_id, n.read_at.is_(None)
            )
        )
        return int(count or 0)

    async def mark_notifications_read(
        self, user_id: UUID, *, ids: list[UUID] | None, now: datetime
    ) -> int:
        n = NotificationRow
        stmt = (
            update(n)
            .where(n.tenant_id == self._tid, n.user_id == user_id, n.read_at.is_(None))
            .values(read_at=now)
        )
        if ids is not None:
            if not ids:
                return 0
            stmt = stmt.where(n.id.in_(ids))
        result = await self._s.execute(stmt.returning(n.id))
        return len(result.all())

    async def clear_message_notice(
        self, conversation_id: UUID, user_id: UUID, *, now: datetime
    ) -> None:
        n = NotificationRow
        await self._s.execute(
            update(n)
            .where(
                n.tenant_id == self._tid,
                n.user_id == user_id,
                n.kind == NotificationKind.MESSAGE.value,
                n.link_id == conversation_id,
                n.read_at.is_(None),
            )
            .values(read_at=now)
        )
