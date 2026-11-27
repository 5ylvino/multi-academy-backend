"""Safeguarding tables. Free text is stored encrypted (AES-GCM through the secret box)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]
_STATUSES = "('open', 'monitoring', 'referred', 'closed')"


class ConcernRow(Base):
    """The app role may update only status, lead and closed_at; the report itself is fixed."""

    __tablename__ = "safeguarding_concerns"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    summary_ciphertext: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reported_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))
    lead_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_safeguarding_concerns_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reported_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_safeguarding_concerns_reporter",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_safeguarding_concerns_lead",
        ),
        CheckConstraint(f"status IN {_STATUSES}", name="status_known"),
        CheckConstraint("severity IN ('low', 'medium', 'high', 'urgent')", name="severity_known"),
        CheckConstraint(
            "category IN ('physical', 'emotional', 'sexual', 'neglect', 'bullying', 'online',"
            " 'self_harm', 'domestic', 'attendance', 'other')",
            name="category_known",
        ),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="closed_has_date"),
        Index("ix_safeguarding_concerns_student", "tenant_id", "student_id", "id"),
        Index("ix_safeguarding_concerns_status", "tenant_id", "status", "id"),
        Index("ix_safeguarding_concerns_reporter", "tenant_id", "reported_by", "id"),
    )


class EntryRow(Base):
    """The chronology. Append-only for the app role."""

    __tablename__ = "safeguarding_entries"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    concern_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    text_ciphertext: Mapped[str] = mapped_column(Text)
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agency: Mapped[str | None] = mapped_column(String(120))
    agency_reference: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str | None] = mapped_column(String(12))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "concern_id"],
            ["safeguarding_concerns.tenant_id", "safeguarding_concerns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["tenant_id", "by"], _USER_FK, ondelete="RESTRICT"),
        CheckConstraint(
            "kind IN ('note', 'parent_contact', 'referral', 'status', 'assigned')",
            name="kind_known",
        ),
        CheckConstraint(f"status IS NULL OR status IN {_STATUSES}", name="status_known"),
        CheckConstraint("kind <> 'referral' OR agency IS NOT NULL", name="referral_has_agency"),
        Index("ix_safeguarding_entries_concern", "tenant_id", "concern_id", "id"),
    )
