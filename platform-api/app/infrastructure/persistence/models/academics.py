"""Academics tables. All tenant-scoped with RLS; ``tenant_id`` leads every key."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class SectionRow(Base):
    """A group of levels under its own academic heads (e.g. "Nursery and Primary")."""

    __tablename__ = "academic_sections"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    code: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index("uq_academic_sections_code", "tenant_id", "code", unique=True),
    )


class SectionLevelRow(Base):
    """Which section holds each level. The key makes a level belong to one section only."""

    __tablename__ = "academic_section_levels"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    school_level: Mapped[str] = mapped_column(String(16), primary_key=True)
    section_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["academic_sections.tenant_id", "academic_sections.id"],
            ondelete="CASCADE",
            name="fk_academic_section_levels_section",
        ),
        CheckConstraint("school_level IN ('nursery', 'primary', 'secondary')", name="level_known"),
        Index("ix_academic_section_levels_section", "tenant_id", "section_id"),
    )


class SectionStaffRow(Base):
    """A head, teacher or other staff member working in a section."""

    __tablename__ = "academic_section_staff"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    section_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    assigned_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["academic_sections.tenant_id", "academic_sections.id"],
            ondelete="RESTRICT",
            name="fk_academic_section_staff_section",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
            name="fk_academic_section_staff_user",
        ),
        Index("ix_academic_section_staff_user", "tenant_id", "user_id"),
    )


class SectionRoleRow(Base):
    """A service role (bursar, nurse, ...) that this school runs per section rather than for
    the whole school. The owner chooses at sign-up."""

    __tablename__ = "academic_section_roles"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        CheckConstraint(
            "role IN ('bursar', 'nurse', 'guard', 'driver', 'administrative_staff')",
            name="role_known",
        ),
    )


class SectionFeatureRow(Base):
    """A feature switched on for a section. Sections run different activities, so each has
    its own set (boarding, for example, is usually only in secondary)."""

    __tablename__ = "academic_section_features"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    section_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    feature: Mapped[str] = mapped_column(String(32), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "section_id"],
            ["academic_sections.tenant_id", "academic_sections.id"],
            ondelete="CASCADE",
            name="fk_academic_section_features_section",
        ),
    )
