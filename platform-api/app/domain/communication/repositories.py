"""Communication persistence, scoped to the current school."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.communication.announcements import Announcement, Audience
from app.domain.communication.conversations import Conversation, Message
from app.domain.communication.inbox import Notification
from app.domain.shared.value_objects import Page


@dataclass(frozen=True, slots=True)
class FeedItem:
    announcement: Announcement
    read_at: datetime | None


@dataclass(frozen=True, slots=True)
class ManagedAnnouncement:
    announcement: Announcement
    reads: int


@dataclass(frozen=True, slots=True)
class Contact:
    user_id: UUID
    email: str
    full_name: str


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: Conversation
    unread: int
    last_message: Message | None


@dataclass(frozen=True, slots=True)
class Reader:
    """Who the feed is for: their roles and the classes they belong to (as a student, through
    a child, or by teaching it)."""

    user_id: UUID
    roles: frozenset[str]
    class_ids: frozenset[UUID]


class CommunicationRepository(Protocol):
    # announcements
    async def add_announcement(self, announcement: Announcement) -> None: ...

    async def get_announcement(
        self, announcement_id: UUID, *, for_update: bool = False
    ) -> Announcement | None: ...

    async def save_announcement(self, announcement: Announcement) -> None: ...

    async def feed(
        self, reader: Reader, *, now: datetime, pinned: bool, cursor: str | None, limit: int
    ) -> Page[FeedItem]:
        """Live announcements the reader is in the audience of, newest first."""
        ...

    async def unread_announcements(self, reader: Reader, *, now: datetime) -> int: ...

    async def mark_announcement_read(
        self, announcement_id: UUID, user_id: UUID, *, now: datetime
    ) -> bool:
        """True the first time; later calls change nothing."""
        ...

    async def managed(
        self, *, state: str | None, now: datetime, cursor: str | None, limit: int
    ) -> Page[ManagedAnnouncement]: ...

    async def audience_contacts(
        self, audience: Audience, *, session_id: UUID | None
    ) -> list[Contact]:
        """Active people in the audience, for urgent emails. Class filters use the given
        academic session's enrolments and teaching assignments."""
        ...

    # conversations
    async def add_conversation(self, conversation: Conversation) -> None: ...

    async def get_conversation(
        self, conversation_id: UUID, *, for_update: bool = False
    ) -> Conversation | None: ...

    async def save_conversation(self, conversation: Conversation) -> None:
        """Store the header and append ``new_messages``."""
        ...

    async def messages(
        self, conversation_id: UUID, *, before: str | None, limit: int
    ) -> Page[Message]:
        """Newest first; ``next_cursor`` pages back in time."""
        ...

    async def conversations_of(
        self, user_id: UUID, *, cursor: str | None, limit: int
    ) -> Page[ConversationSummary]: ...

    async def mark_conversation_read(
        self, conversation_id: UUID, user_id: UUID, *, now: datetime
    ) -> None: ...

    # inbox
    async def notify(self, notifications: list[Notification]) -> None:
        """Insert. A message notice replaces the same person's unread notice for that
        conversation instead of stacking up."""
        ...

    async def notifications(
        self, user_id: UUID, *, unread_only: bool, cursor: str | None, limit: int
    ) -> Page[Notification]: ...

    async def unread_notifications(self, user_id: UUID) -> int: ...

    async def mark_notifications_read(
        self, user_id: UUID, *, ids: list[UUID] | None, now: datetime
    ) -> int:
        """Mark these (or, with None, all) of the user's notices read; returns how many."""
        ...

    async def clear_message_notice(
        self, conversation_id: UUID, user_id: UUID, *, now: datetime
    ) -> None:
        """Reading a conversation marks its notice read too."""
        ...
