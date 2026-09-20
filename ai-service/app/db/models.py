import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class AiConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_ai_conversations_tenant_feature", "tenant_id", "feature"),)


class AiMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_ai_messages_conversation_created", "conversation_id", "created_at"),)


class AiDocument(Base):
    __tablename__ = "ai_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="staff")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_ai_documents_tenant_source", "tenant_id", "source_type", "source_id"),)


class AiDocumentChunk(Base):
    __tablename__ = "ai_document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    pinecone_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_preview: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_ai_document_chunks_doc_idx", "document_id", "chunk_index"),)


class AiTutorSession(Base):
    __tablename__ = "ai_tutor_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="explain")
    mastery_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiz_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiTutorMessage(Base):
    __tablename__ = "ai_tutor_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiTopicMastery(Base):
    __tablename__ = "ai_topic_mastery"
    __table_args__ = (
        Index("ix_ai_topic_mastery_tenant_student", "tenant_id", "student_id"),
        UniqueConstraint("tenant_id", "student_id", "subject_id", "topic_key", name="uq_topic_mastery"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mastery_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiTopicMasteryRollup(Base):
    __tablename__ = "ai_topic_mastery_rollups"
    __table_args__ = (
        Index("ix_ai_mastery_rollups_tenant_class", "tenant_id", "class_id"),
        UniqueConstraint("tenant_id", "class_id", "subject_id", "topic_key", name="uq_mastery_rollup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    student_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    below_threshold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_mastery_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiInterventionPlan(Base):
    __tablename__ = "ai_intervention_plans"
    __table_args__ = (Index("ix_ai_intervention_tenant_teacher", "tenant_id", "teacher_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    teacher_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    class_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    plan_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiReportCommentDraft(Base):
    __tablename__ = "ai_report_comment_drafts"
    __table_args__ = (Index("ix_ai_report_comments_tenant_student", "tenant_id", "student_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    term_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    comment_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiAuditEvent(Base):
    __tablename__ = "ai_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_ai_audit_tenant_created", "tenant_id", "created_at"),)
