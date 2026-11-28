"""Assessment tables: the scheme's parts, grading bands, score sheets, marks and exemptions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]
_LEVELS = "school_level IN ('nursery', 'primary', 'secondary')"


class ComponentRow(Base):
    __tablename__ = "assessment_components"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    school_level: Mapped[str] = mapped_column(String(12))
    code: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(12))
    max_score: Mapped[int] = mapped_column(SmallInteger)
    position: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "school_level", "code", name="uq_assessment_components_code"),
        CheckConstraint(_LEVELS, name="level_known"),
        CheckConstraint("kind IN ('continuous', 'exam')", name="kind_known"),
        CheckConstraint("max_score BETWEEN 1 AND 100", name="max_range"),
    )


class BandRow(Base):
    __tablename__ = "assessment_grade_bands"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    school_level: Mapped[str] = mapped_column(String(12), primary_key=True)
    grade: Mapped[str] = mapped_column(String(3), primary_key=True)
    remark: Mapped[str] = mapped_column(String(40))
    min_score: Mapped[int] = mapped_column(SmallInteger)
    max_score: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (
        CheckConstraint(_LEVELS, name="level_known"),
        CheckConstraint(
            "min_score >= 0 AND min_score <= max_score AND max_score <= 100", name="range"
        ),
    )


class SheetRow(Base):
    __tablename__ = "assessment_score_sheets"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    subject_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["academic_terms.tenant_id", "academic_terms.id"],
            ondelete="RESTRICT",
            name="fk_assessment_score_sheets_term",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "class_id"],
            ["academic_classes.tenant_id", "academic_classes.id"],
            ondelete="RESTRICT",
            name="fk_assessment_score_sheets_class",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subject_id"],
            ["academic_subjects.tenant_id", "academic_subjects.id"],
            ondelete="RESTRICT",
            name="fk_assessment_score_sheets_subject",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "submitted_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_score_sheets_submitter",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approved_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_score_sheets_approver",
        ),
        UniqueConstraint(
            "tenant_id",
            "term_id",
            "class_id",
            "subject_id",
            name="uq_assessment_score_sheets_scope",
        ),
        CheckConstraint("status IN ('draft', 'submitted', 'approved')", name="status_known"),
        CheckConstraint(
            "status = 'draft' OR submitted_by IS NOT NULL", name="submitted_has_submitter"
        ),
        CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_by <> submitted_by)",
            name="two_person_approval",
        ),
        Index("ix_assessment_score_sheets_class", "tenant_id", "term_id", "class_id"),
    )


class MarkRow(Base):
    """Changes only while the sheet is a draft (trigger ``masms_marks_locked``)."""

    __tablename__ = "assessment_marks"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    sheet_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    component_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    entered_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "sheet_id"],
            ["assessment_score_sheets.tenant_id", "assessment_score_sheets.id"],
            ondelete="RESTRICT",
            name="fk_assessment_marks_sheet",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_marks_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            ["assessment_components.tenant_id", "assessment_components.id"],
            ondelete="RESTRICT",
            name="fk_assessment_marks_component",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "entered_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_marks_enterer",
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        Index("ix_assessment_marks_component", "tenant_id", "component_id"),
    )


class ExemptionRow(Base):
    """A student who doesn't take this subject (e.g. an elective); also locked with the sheet."""

    __tablename__ = "assessment_exemptions"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    sheet_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "sheet_id"],
            ["assessment_score_sheets.tenant_id", "assessment_score_sheets.id"],
            ondelete="RESTRICT",
            name="fk_assessment_exemptions_sheet",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_exemptions_student",
        ),
    )


class CommentRow(Base):
    __tablename__ = "assessment_report_comments"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    teacher_comment: Mapped[str | None] = mapped_column(String(500))
    teacher_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    head_comment: Mapped[str | None] = mapped_column(String(500))
    head_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["academic_terms.tenant_id", "academic_terms.id"],
            ondelete="RESTRICT",
            name="fk_assessment_report_comments_term",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_report_comments_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "teacher_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_report_comments_teacher",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "head_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_report_comments_head",
        ),
    )


class ReleaseRow(Base):
    __tablename__ = "assessment_releases"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(SmallInteger)
    released_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["academic_terms.tenant_id", "academic_terms.id"],
            ondelete="RESTRICT",
            name="fk_assessment_releases_term",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "class_id"],
            ["academic_classes.tenant_id", "academic_classes.id"],
            ondelete="RESTRICT",
            name="fk_assessment_releases_class",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "released_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_releases_releaser",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "withdrawn_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_releases_withdrawer",
        ),
        CheckConstraint("status IN ('released', 'withdrawn')", name="status_known"),
        CheckConstraint(
            "status <> 'withdrawn' OR withdraw_reason IS NOT NULL", name="withdrawal_explained"
        ),
    )


class ReportCardRow(Base):
    """The released result for one student and term, as it was released. Never deleted."""

    __tablename__ = "assessment_report_cards"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(SmallInteger)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "term_id", "class_id"],
            [
                "assessment_releases.tenant_id",
                "assessment_releases.term_id",
                "assessment_releases.class_id",
            ],
            ondelete="RESTRICT",
            name="fk_assessment_report_cards_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_assessment_report_cards_student",
        ),
        Index("ix_assessment_report_cards_student", "tenant_id", "student_id", "term_id"),
    )
