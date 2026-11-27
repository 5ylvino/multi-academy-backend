"""Platform tables: plans, flags and per-school flag overrides."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from masms.infrastructure.persistence.sqlalchemy.base import Base


class PlanRow(Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    features: Mapped[list[str]] = mapped_column(ARRAY(String(100)), server_default=text("'{}'"))
    student_limit: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class FeatureFlagRow(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    default_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    kill_switch: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    rollout_percent: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class TenantFlagOverrideRow(Base):
    __tablename__ = "tenant_flag_overrides"

    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    flag_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("feature_flags.key", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
