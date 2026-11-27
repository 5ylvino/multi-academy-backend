"""Transport tables: routes, stops, which students ride each route, and boarding events."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]
_ROUTE_FK = ["transport_routes.tenant_id", "transport_routes.id"]


class RouteRow(Base):
    __tablename__ = "transport_routes"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    code: Mapped[str] = mapped_column(String(12))
    vehicle_plate: Mapped[str | None] = mapped_column(String(15))
    capacity: Mapped[int] = mapped_column(SmallInteger)
    driver_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "driver_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_transport_routes_driver",
        ),
        UniqueConstraint("tenant_id", "code", name="uq_transport_routes_code"),
        CheckConstraint("capacity BETWEEN 1 AND 120", name="capacity_range"),
        Index("ix_transport_routes_driver", "tenant_id", "driver_id"),
    )


class StopRow(Base):
    """Stops are retired, not deleted, so boarding history keeps pointing at them."""

    __tablename__ = "transport_stops"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    route_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(SmallInteger)
    morning_at: Mapped[time | None] = mapped_column(Time)
    afternoon_at: Mapped[time | None] = mapped_column(Time)
    landmark: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "route_id"], _ROUTE_FK, ondelete="RESTRICT"),
        # Lets an assignment's (route, stop) pair be checked by a foreign key.
        UniqueConstraint("tenant_id", "route_id", "id", name="uq_transport_stops_route_stop"),
        CheckConstraint("position BETWEEN 1 AND 40", name="position_range"),
    )


class AssignmentRow(Base):
    __tablename__ = "transport_assignments"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    route_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    stop_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_transport_assignments_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "route_id", "stop_id"],
            ["transport_stops.tenant_id", "transport_stops.route_id", "transport_stops.id"],
            ondelete="RESTRICT",
            name="fk_transport_assignments_stop",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "by"], _USER_FK, ondelete="RESTRICT", name="fk_transport_assignments_by"
        ),
        Index("ix_transport_assignments_route", "tenant_id", "route_id"),
    )


class BoardingRow(Base):
    """Append-only for the app role."""

    __tablename__ = "transport_boardings"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    route_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    stop_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    direction: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(8))
    day: Mapped[date] = mapped_column(Date)
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_transport_boardings_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "route_id"],
            _ROUTE_FK,
            ondelete="RESTRICT",
            name="fk_transport_boardings_route",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "stop_id"],
            ["transport_stops.tenant_id", "transport_stops.id"],
            ondelete="RESTRICT",
            name="fk_transport_boardings_stop",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "by"], _USER_FK, ondelete="RESTRICT", name="fk_transport_boardings_by"
        ),
        CheckConstraint("direction IN ('to_school', 'from_school')", name="direction_known"),
        CheckConstraint("kind IN ('boarded', 'dropped', 'absent')", name="kind_known"),
        Index("ix_transport_boardings_trip", "tenant_id", "route_id", "day", "direction"),
        Index("ix_transport_boardings_student", "tenant_id", "student_id", "id"),
    )
