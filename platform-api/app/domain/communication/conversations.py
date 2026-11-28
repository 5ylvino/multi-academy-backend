"""Conversations between parents and staff, and among staff.

Safeguarding rules, by design:

- Messages are never edited or deleted, by anyone. What was said stays on record.
- Staff can't start a private conversation with a student.
- Parents can talk to staff, not to other parents, and only about their own children.
- School leaders can open any conversation to review it; each time is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id

MAX_PARTICIPANTS = 20
MAX_BODY = 5_000


def _body(text: str) -> str:
    clean = text.strip()
    if not clean:
        raise ValidationFailed("Write a message.", details={"field": "body"})
    if len(clean) > MAX_BODY:
        raise ValidationFailed(
            f"Keep messages under {MAX_BODY} characters.", details={"field": "body"}
        )
    return clean


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    sender_id: UUID
    body: str
    sent_at: datetime


@dataclass(kw_only=True, eq=False)
class Conversation:
    id: UUID
    tenant_id: UUID
    subject: str
    student_id: UUID | None
    created_by: UUID
    created_at: datetime
    last_message_at: datetime
    participants: frozenset[UUID]
    message_count: int = 0
    new_messages: list[Message] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        *,
        tenant_id: UUID,
        subject: str,
        student_id: UUID | None,
        by: UUID,
        others: set[UUID],
        first_message: str,
        now: datetime,
    ) -> Conversation:
        clean = " ".join(subject.split())
        if not 3 <= len(clean) <= 160:
            raise ValidationFailed(
                "Give it a subject (3-160 characters).", details={"field": "subject"}
            )
        people = set(others) - {by}
        if not people:
            raise ValidationFailed("Add someone to talk to.", details={"field": "participant_ids"})
        if len(people) + 1 > MAX_PARTICIPANTS:
            raise ValidationFailed(
                f"At most {MAX_PARTICIPANTS} people in one conversation.",
                details={"field": "participant_ids"},
            )
        conversation = cls(
            id=new_id(),
            tenant_id=tenant_id,
            subject=clean,
            student_id=student_id,
            created_by=by,
            created_at=now,
            last_message_at=now,
            participants=frozenset(people | {by}),
        )
        conversation.send(by=by, body=first_message, now=now)
        return conversation

    def send(self, *, by: UUID, body: str, now: datetime) -> Message:
        if by not in self.participants:
            raise ValidationFailed("You're not in this conversation.", code="not_participant")
        message = Message(
            id=new_id(),
            tenant_id=self.tenant_id,
            conversation_id=self.id,
            sender_id=by,
            body=_body(body),
            sent_at=now,
        )
        self.new_messages.append(message)
        self.message_count += 1
        self.last_message_at = now
        return message
