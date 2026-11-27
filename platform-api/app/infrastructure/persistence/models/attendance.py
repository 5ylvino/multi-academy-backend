"""Attendance tables. Tenant-scoped with RLS.

Register tables carry ``day`` in every key so they can become range-partitioned by date
(Phase 5) without changing constraints.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class AttendanceSettingsRow(Base):
    __tablename__ = "attendance_settings"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    window_opens: Mapped[time] = mapped_column(Time)
    late_after: Mapped[time] = mapped_column(Time)
    window_closes: Mapped[time] = mapped_column(Time)
    business_days: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    check_in_roles: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    sites: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    require_location: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    kiosk_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),)


class StudentMarkRow(Base):
    __tablename__ = "attendance_student_marks"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    class_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16))
    marked_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(300))
    corrected_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_reason: Mapped[str | None] = mapped_column(String(500))

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
        # One daily mark per student, whichever class took it.
        UniqueConstraint(
            "tenant_id", "student_id", "day", name="uq_attendance_student_marks_student_day"
        ),
        Index("ix_attendance_student_marks_class_day", "tenant_id", "class_id", "day"),
    )


class StaffCheckInRow(Base):
    __tablename__ = "attendance_staff_check_ins"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    staff_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16))
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    site_name: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "staff_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "staff_id", "day", name="uq_attendance_staff_check_ins_staff_day"
        ),
    )
