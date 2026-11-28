"""Communication tables: announcements, conversations and the notification inbox."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]


class AnnouncementRow(Base):
    __tablename__ = "communication_announcements"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    audience_roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    class_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), server_default=text("'{}'")
    )
    priority: Mapped[str] = mapped_column(String(8))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "author_id"], _USER_FK, ondelete="RESTRICT"),
        CheckConstraint("priority IN ('normal', 'urgent')", name="priority_known"),
        CheckConstraint("cardinality(audience_roles) > 0", name="has_audience"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 10000", name="body_length"),
        CheckConstraint("expires_at IS NULL OR expires_at > publish_at", name="ends_after_start"),
        Index("ix_communication_announcements_feed", "tenant_id", "publish_at", "id"),
    )


class AnnouncementReadRow(Base):
    __tablename__ = "communication_announcement_reads"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    announcement_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "announcement_id"],
            ["communication_announcements.tenant_id", "communication_announcements.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "user_id"], _USER_FK, ondelete="CASCADE"),
        Index("ix_communication_announcement_reads_user", "tenant_id", "user_id"),
    )


class ConversationRow(Base):
    __tablename__ = "communication_conversations"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    subject: Mapped[str] = mapped_column(String(160))
    student_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_communication_conversations_created_by",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_communication_conversations_student",
        ),
        CheckConstraint("message_count >= 1", name="has_messages"),
        Index(
            "ix_communication_conversations_student",
            "tenant_id",
            "student_id",
            postgresql_where=text("student_id IS NOT NULL"),
        ),
    )


class ParticipantRow(Base):
    __tablename__ = "communication_participants"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["communication_conversations.tenant_id", "communication_conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "user_id"], _USER_FK, ondelete="RESTRICT"),
        Index("ix_communication_participants_user", "tenant_id", "user_id"),
    )


class MessageRow(Base):
    """Append-only for the app role: messages are never edited or deleted."""

    __tablename__ = "communication_messages"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    sender_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "sender_id"],
            [
                "communication_participants.tenant_id",
                "communication_participants.conversation_id",
                "communication_participants.user_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("char_length(body) BETWEEN 1 AND 5000", name="body_length"),
        Index("ix_communication_messages_thread", "tenant_id", "conversation_id", "id"),
    )


class NotificationRow(Base):
    __tablename__ = "communication_notifications"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(String(280))
    link_type: Mapped[str | None] = mapped_column(String(32))
    link_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "user_id"], _USER_FK, ondelete="CASCADE"),
        CheckConstraint("kind IN ('message', 'payslip', 'general')", name="kind_known"),
        Index("ix_communication_notifications_user", "tenant_id", "user_id", "id"),
        Index(
            "ix_communication_notifications_unread",
            "tenant_id",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index(
            "uq_communication_notifications_open_message",
            "tenant_id",
            "user_id",
            "link_id",
            unique=True,
            postgresql_where=text("kind = 'message' AND read_at IS NULL"),
        ),
    )
