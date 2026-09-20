from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["global", "plan", "tenant"])
    requires_providers: Mapped[list] = mapped_column(JSON, default=list)
    min_plan: Mapped[str | None] = mapped_column(String(16), nullable=True)  # basic|premium|elite
    status: Mapped[str] = mapped_column(String(16), default="stable")  # stable|beta|deprecated
    # Global kill switch: highest precedence, forces the feature off everywhere.
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deprecation workflow (Phase C): stable → deprecated → removal_date announced
    deprecation_note: Mapped[str] = mapped_column(Text, default="")
    removal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FlagOverride(Base):
    """Global / plan / tenant override for a flag. Tenant overrides win over plan."""

    __tablename__ = "flag_overrides"
    __table_args__ = (UniqueConstraint("feature_key", "scope", "plan_id", "tenant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(16))  # global | plan | tenant
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
