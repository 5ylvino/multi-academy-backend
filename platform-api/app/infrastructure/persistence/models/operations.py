"""Operations tables: clinic records and visits, the gate (collectors, releases, visitors)."""

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
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base

_USER_FK = ["school_users.tenant_id", "school_users.id"]


class HealthRecordRow(Base):
    """``details_ciphertext`` and ``alert_ciphertext`` are AES-GCM sealed JSON / text."""

    __tablename__ = "clinic_health_records"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    blood_group: Mapped[str | None] = mapped_column(String(3))
    genotype: Mapped[str | None] = mapped_column(String(2))
    details_ciphertext: Mapped[str] = mapped_column(Text)
    alert_ciphertext: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_clinic_records_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_clinic_records_editor",
        ),
    )


class ClinicVisitRow(Base):
    __tablename__ = "clinic_visits"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temperature_dc: Mapped[int | None] = mapped_column(SmallInteger)
    outcome: Mapped[str | None] = mapped_column(String(24))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    notes_ciphertext: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_clinic_visits_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recorded_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_clinic_visits_nurse",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('returned_to_class', 'rested_in_clinic', 'sent_home',"
            " 'collected_by_parent', 'referred_to_hospital')",
            name="outcome_known",
        ),
        CheckConstraint("(outcome IS NULL) = (left_at IS NULL)", name="closed_has_time"),
        CheckConstraint(
            "temperature_dc IS NULL OR temperature_dc BETWEEN 300 AND 450", name="temperature_range"
        ),
        Index("ix_clinic_visits_student", "tenant_id", "student_id", "id"),
        Index("ix_clinic_visits_open", "tenant_id", "id", postgresql_where=text("outcome IS NULL")),
    )


class CollectorRow(Base):
    __tablename__ = "gate_collectors"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    relationship: Mapped[str] = mapped_column(String(40))
    phone: Mapped[str | None] = mapped_column(String(20))
    id_document: Mapped[str | None] = mapped_column(String(80))
    valid_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    added_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_gate_collectors_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "added_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_gate_collectors_adder",
        ),
        Index("ix_gate_collectors_student", "tenant_id", "student_id"),
    )


class ReleaseRow(Base):
    """Append-only for the app role."""

    __tablename__ = "gate_releases"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(10))
    guardian_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    collector_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    collector_name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(300))
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_gate_releases_student",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "guardian_id"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_gate_releases_guardian",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "collector_id"],
            ["gate_collectors.tenant_id", "gate_collectors.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "by"], _USER_FK, ondelete="RESTRICT", name="fk_gate_releases_guard"
        ),
        CheckConstraint("status IN ('released', 'refused')", name="status_known"),
        CheckConstraint(
            "status <> 'released' OR num_nonnulls(guardian_id, collector_id) = 1",
            name="released_to_someone",
        ),
        CheckConstraint(
            "status <> 'refused'"
            " OR (guardian_id IS NULL AND collector_id IS NULL AND note IS NOT NULL)",
            name="refusal_explained",
        ),
        Index("ix_gate_releases_student", "tenant_id", "student_id", "id"),
        Index("ix_gate_releases_time", "tenant_id", "at"),
    )


class VisitorRow(Base):
    __tablename__ = "gate_visits"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    organisation: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(200))
    host_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    badge: Mapped[str | None] = mapped_column(String(20))
    vehicle: Mapped[str | None] = mapped_column(String(20))
    signed_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signed_in_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    signed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_out_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "host_id"], _USER_FK, ondelete="RESTRICT", name="fk_gate_visits_host"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "signed_in_by"],
            _USER_FK,
            ondelete="RESTRICT",
            name="fk_gate_visits_guard",
        ),
        CheckConstraint(
            "signed_out_at IS NULL OR signed_out_at >= signed_in_at", name="out_after_in"
        ),
        Index("ix_gate_visits_time", "tenant_id", "signed_in_at"),
        Index(
            "ix_gate_visits_on_site",
            "tenant_id",
            "signed_in_at",
            postgresql_where=text("signed_out_at IS NULL"),
        ),
    )
