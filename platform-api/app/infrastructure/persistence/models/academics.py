"""Academics tables. All tenant-scoped with RLS; ``tenant_id`` leads every key."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class AcademicSessionRow(Base):
    __tablename__ = "academic_sessions"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index(
            "uq_academic_sessions_current",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class TermRow(Base):
    __tablename__ = "academic_terms"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["academic_sessions.tenant_id", "academic_sessions.id"],
            ondelete="CASCADE",
        ),
        Index("uq_academic_terms_session_code", "tenant_id", "session_id", "code", unique=True),
        Index(
            "uq_academic_terms_current",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class SchoolClassRow(Base):
    __tablename__ = "academic_classes"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))
    school_level: Mapped[str] = mapped_column(String(16))
    class_teacher_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    capacity: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index("uq_academic_classes_code", "tenant_id", "code", unique=True),
    )


class SubjectRow(Base):
    __tablename__ = "academic_subjects"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))
    school_level: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index("uq_academic_subjects_code_level", "tenant_id", "school_level", "code", unique=True),
    )


class ClassSubjectRow(Base):
    __tablename__ = "academic_class_subjects"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    teacher_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "class_id"],
            ["academic_classes.tenant_id", "academic_classes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subject_id"],
            ["academic_subjects.tenant_id", "academic_subjects.id"],
            ondelete="CASCADE",
        ),
        Index("ix_academic_class_subjects_subject", "tenant_id", "subject_id"),
    )


class EnrolmentRow(Base):
    __tablename__ = "academic_enrolments"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "class_id"],
            ["academic_classes.tenant_id", "academic_classes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["academic_sessions.tenant_id", "academic_sessions.id"],
            ondelete="CASCADE",
        ),
        Index(
            "uq_academic_enrolments_active",
            "tenant_id",
            "student_id",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_academic_enrolments_roster", "tenant_id", "class_id", "session_id", "status"),
    )
