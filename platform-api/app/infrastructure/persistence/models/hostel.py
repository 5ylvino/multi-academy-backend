"""Boarding tables: hostels, rooms, where each boarder sleeps, and exeats."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]


class HostelRow(Base):
    __tablename__ = "hostels"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(6))
    warden_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "warden_id"], _USER_FK, ondelete="RESTRICT", name="fk_hostels_warden"
        ),
        UniqueConstraint("tenant_id", "name", name="uq_hostels_name"),
        CheckConstraint("kind IN ('boys', 'girls', 'mixed')", name="kind_known"),
    )


class RoomRow(Base):
    __tablename__ = "hostel_rooms"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    hostel_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(40))
    capacity: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "hostel_id"],
            ["hostels.tenant_id", "hostels.id"],
            ondelete="RESTRICT",
            name="fk_hostel_rooms_hostel",
        ),
        CheckConstraint("capacity BETWEEN 1 AND 40", name="capacity_range"),
        Index(
            "uq_hostel_rooms_name",
            "tenant_id",
            "hostel_id",
            "name",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class AllocationRow(Base):
    __tablename__ = "hostel_allocations"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    room_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    bed: Mapped[str | None] = mapped_column(String(10))
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_hostel_allocations_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "room_id"],
            ["hostel_rooms.tenant_id", "hostel_rooms.id"],
            ondelete="RESTRICT",
            name="fk_hostel_allocations_room",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "by"], _USER_FK, ondelete="RESTRICT", name="fk_hostel_allocations_by"
        ),
        Index("ix_hostel_allocations_room", "tenant_id", "room_id"),
        Index(
            "uq_hostel_allocations_bed",
            "tenant_id",
            "room_id",
            "bed",
            unique=True,
            postgresql_where=text("bed IS NOT NULL"),
        ),
    )


class ExeatRow(Base):
    """Never deleted: the record of every time a boarder left and came back."""

    __tablename__ = "hostel_exeats"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    requested_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(String(500))
    collector_name: Mapped[str] = mapped_column(String(120))
    leave_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    return_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_hostel_exeats_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_hostel_exeats_requester",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "decided_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_hostel_exeats_decider",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'out', 'returned')",
            name="status_known",
        ),
        CheckConstraint("return_by > leave_at", name="returns_after_leaving"),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by", name="two_person_decision"
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'out', 'returned', 'rejected') OR decided_by IS NOT NULL",
            name="decided_has_decider",
        ),
        # One open exeat per boarder at a time.
        Index(
            "uq_hostel_exeats_open",
            "tenant_id",
            "student_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved', 'out')"),
        ),
        Index("ix_hostel_exeats_student", "tenant_id", "student_id", "id"),
        Index(
            "ix_hostel_exeats_out",
            "tenant_id",
            "return_by",
            postgresql_where=text("status = 'out'"),
        ),
    )
