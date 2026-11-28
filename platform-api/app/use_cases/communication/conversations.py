"""Conversations between parents and staff, and the notification inbox.

Who may talk to whom (see the domain module for the safeguarding reasons):

- Staff with ``correspondence:manage`` can start a conversation with other staff and with
  parents. Students can't be added.
- Parents with ``correspondence:manage`` can start one with staff only. When it is about a child,
  it must be their own child.
- When a conversation is about a student, every parent in it must be that student's guardian.
- Anyone in a conversation can reply. School leaders with ``conversations:oversee`` can read any
  conversation; each time is recorded in the activity log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.communication.conversations import Conversation, Message
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.identity.user import UserAccount, UserStatus
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.communication.announcements import reader_of
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, RateLimiter, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

_STUDENT = SchoolRole.STUDENT.value
_PARENT = SchoolRole.PARENT.value


@dataclass(frozen=True, slots=True)
class Person:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class MessageView:
    id: UUID
    sender_id: UUID
    sender_name: str
    body: str
    sent_at: datetime
    mine: bool


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: UUID
    subject: str
    student_id: UUID | None
    participants: list[Person]
    created_by: UUID
    created_at: datetime
    last_message_at: datetime
    message_count: int
    unread: int
    last_message: MessageView | None


@dataclass(frozen=True, slots=True)
class ThreadView:
    conversation: ConversationView
    messages: list[MessageView]
    #: Pass as ``before`` to load older messages.
    older_cursor: str | None


@dataclass(frozen=True, slots=True)
class StartCommand:
    subject: str
    participant_ids: list[UUID]
    body: str
    student_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NotificationView:
    id: UUID
    kind: str
    title: str
    body: str
    link_type: str | None
    link_id: UUID | None
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def of(cls, n: Notification) -> NotificationView:
        return cls(
            id=n.id,
            kind=n.kind.value,
            title=n.title,
            body=n.body,
            link_type=n.link_type,
            link_id=n.link_id,
            created_at=n.created_at,
            read_at=n.read_at,
        )


class Conversations:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, limiter: RateLimiter
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._limiter = limiter

    async def start(self, actor: Principal, cmd: StartCommand) -> ThreadView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("correspondence:manage")
        if _STUDENT in actor.roles and not actor.roles - {_STUDENT}:
            raise PermissionDenied("Students can't start conversations.")
        await self._limiter.hit(f"conv-start:{actor.user_id}", limit=20, window_seconds=3600)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            me = await _member(uow, tenant_id, actor.user_id)
            others = [await _member(uow, tenant_id, uid) for uid in set(cmd.participant_ids)]
            await self._check_people(uow, actor, me, others, cmd.student_id, tenant_id)
            conversation = Conversation.start(
                tenant_id=tenant_id,
                subject=cmd.subject,
                student_id=cmd.student_id,
                by=actor.user_id,
                others={o.id for o in others},
                first_message=cmd.body,
                now=now,
            )
            first = conversation.new_messages[0]
            await uow.communication.add_conversation(conversation)
            await _notify_others(uow, conversation, first, sender=me.full_name)
            uow.audit(
                audit.by(
                    actor,
                    "communication.conversation_started",
                    target_type="conversation",
                    target_id=conversation.id,
                    subject=conversation.subject,
                    participants=sorted(str(p) for p in conversation.participants),
                    student_id=str(cmd.student_id) if cmd.student_id else None,
                )
            )
            view = await _thread(uow, conversation, actor.user_id, unread=0)
            await uow.commit()
        return view

    async def reply(self, actor: Principal, conversation_id: UUID, *, body: str) -> MessageView:
        actor.require_realm(Realm.SCHOOL)
        await self._limiter.hit(f"conv-msg:{actor.user_id}", limit=30, window_seconds=60)
        now = self._clock.now()
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            conversation = await uow.communication.get_conversation(
                conversation_id, for_update=True
            )
            if conversation is None or actor.user_id not in conversation.participants:
                raise NotFound("Conversation not found.")
            me = await _member(uow, tenant_id, actor.user_id)
            message = conversation.send(by=actor.user_id, body=body, now=now)
            await uow.communication.save_conversation(conversation)
            await uow.communication.mark_conversation_read(conversation.id, actor.user_id, now=now)
            await _notify_others(uow, conversation, message, sender=me.full_name)
            await uow.commit()  # audit: not needed. Messages are append-only records themselves.
        return MessageView(
            id=message.id,
            sender_id=message.sender_id,
            sender_name=me.full_name,
            body=message.body,
            sent_at=message.sent_at,
            mine=True,
        )

    async def open(
        self, actor: Principal, conversation_id: UUID, *, before: str | None, limit: int | None
    ) -> ThreadView:
        """Messages newest first. Opening marks the conversation read for a participant; a
        leader reviewing one they aren't in is recorded instead."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            conversation = await uow.communication.get_conversation(conversation_id)
            if conversation is None:
                raise NotFound("Conversation not found.")
            member = actor.user_id in conversation.participants
            if not member:
                if not actor.permissions.allows("conversations:oversee"):
                    raise NotFound("Conversation not found.")
                if before is None:
                    uow.audit(
                        audit.by(
                            actor,
                            "communication.conversation_reviewed",
                            target_type="conversation",
                            target_id=conversation.id,
                            subject=conversation.subject,
                        )
                    )
            view = await _thread(
                uow, conversation, actor.user_id, unread=0, before=before, limit=clamp_limit(limit)
            )
            if member:
                await uow.communication.mark_conversation_read(
                    conversation.id, actor.user_id, now=now
                )
                await uow.communication.clear_message_notice(
                    conversation.id, actor.user_id, now=now
                )
            await uow.commit()
        return view

    async def mine(
        self, actor: Principal, *, cursor: str | None, limit: int | None
    ) -> Page[ConversationView]:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.communication.conversations_of(
                actor.user_id, cursor=cursor, limit=clamp_limit(limit)
            )
            people = {p for s in page.items for p in s.conversation.participants}
            names = await uow.users.school_names(people)
        return Page(
            items=[
                _summary(s.conversation, names, actor.user_id, s.unread, s.last_message)
                for s in page.items
            ],
            next_cursor=page.next_cursor,
        )

    # ---- inbox ------------------------------------------------------------------------------

    async def notifications(
        self, actor: Principal, *, unread_only: bool, cursor: str | None, limit: int | None
    ) -> Page[NotificationView]:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.communication.notifications(
                actor.user_id, unread_only=unread_only, cursor=cursor, limit=clamp_limit(limit)
            )
        return Page(
            items=[NotificationView.of(n) for n in page.items], next_cursor=page.next_cursor
        )

    async def unread_counts(self, actor: Principal) -> tuple[int, int]:
        """Unread notifications and unread announcements, for the badge in the app bar."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            notes = await uow.communication.unread_notifications(actor.user_id)
            reader = await reader_of(uow, actor)
            news = await uow.communication.unread_announcements(reader, now=now)
        return notes, news

    async def mark_read(self, actor: Principal, ids: list[UUID] | None) -> int:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            count = await uow.communication.mark_notifications_read(
                actor.user_id, ids=ids, now=self._clock.now()
            )
            await uow.commit()  # audit: not needed. Read marks on one's own notices.
        return count

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    async def _check_people(
        uow: UnitOfWork,
        actor: Principal,
        me: UserAccount,
        others: list[UserAccount],
        student_id: UUID | None,
        tenant_id: UUID,
    ) -> None:
        staff_me = bool(me.roles - {_STUDENT, _PARENT})
        for other in others:
            if not other.roles - {_STUDENT, _PARENT}:
                if _STUDENT in other.roles:
                    raise ValidationFailed(
                        "Students can't be added to a conversation.",
                        code="student_participant",
                        details={"user_id": str(other.id)},
                    )
                if not staff_me:
                    raise ValidationFailed(
                        "Parents can message school staff.",
                        code="parent_to_parent",
                        details={"user_id": str(other.id)},
                    )
        if student_id is None:
            return
        student = await uow.users.get(student_id)
        if student is None or student.tenant_id != tenant_id or _STUDENT not in student.roles:
            raise NotFound("Student not found.")
        guardians = {g.parent_id for g in await uow.guardians.guardians_of(student_id)}
        parents = [p for p in [me, *others] if not p.roles - {_STUDENT, _PARENT}]
        for parent in parents:
            if parent.id not in guardians:
                raise PermissionDenied(
                    "Every parent in the conversation must be this student's guardian.",
                    code="not_guardian",
                )
        if not staff_me and actor.user_id not in guardians:
            raise PermissionDenied(
                "You can only write about your own children.", code="not_guardian"
            )


async def _member(uow: UnitOfWork, tenant_id: UUID, user_id: UUID) -> UserAccount:
    user = await uow.users.get(user_id)
    if (
        user is None
        or user.realm is not Realm.SCHOOL
        or user.tenant_id != tenant_id
        or user.status is not UserStatus.ACTIVE
    ):
        raise NotFound("Person not found.", details={"user_id": str(user_id)})
    return user


async def _notify_others(
    uow: UnitOfWork, conversation: Conversation, message: Message, *, sender: str
) -> None:
    await uow.communication.notify(
        [
            Notification.new(
                tenant_id=conversation.tenant_id,
                user_id=person,
                kind=NotificationKind.MESSAGE,
                title=f"New message from {sender}",
                body=conversation.subject,
                link_type="conversation",
                link_id=conversation.id,
                now=message.sent_at,
            )
            for person in sorted(conversation.participants - {message.sender_id})
        ]
    )


async def _thread(
    uow: UnitOfWork,
    conversation: Conversation,
    me: UUID,
    *,
    unread: int,
    before: str | None = None,
    limit: int = 50,
) -> ThreadView:
    page = await uow.communication.messages(conversation.id, before=before, limit=limit)
    names = await uow.users.school_names(set(conversation.participants))
    messages = [_message(m, names, me) for m in page.items]
    return ThreadView(
        conversation=_summary(
            conversation, names, me, unread, page.items[0] if page.items else None
        ),
        messages=messages,
        older_cursor=page.next_cursor,
    )


def _message(m: Message, names: dict[UUID, str], me: UUID) -> MessageView:
    return MessageView(
        id=m.id,
        sender_id=m.sender_id,
        sender_name=names.get(m.sender_id, ""),
        body=m.body,
        sent_at=m.sent_at,
        mine=m.sender_id == me,
    )


def _summary(
    c: Conversation, names: dict[UUID, str], me: UUID, unread: int, last: Message | None
) -> ConversationView:
    return ConversationView(
        id=c.id,
        subject=c.subject,
        student_id=c.student_id,
        participants=sorted(
            (Person(id=p, name=names.get(p, "")) for p in c.participants),
            key=lambda p: p.name.lower(),
        ),
        created_by=c.created_by,
        created_at=c.created_at,
        last_message_at=c.last_message_at,
        message_count=c.message_count,
        unread=unread,
        last_message=_message(last, names, me) if last else None,
    )
