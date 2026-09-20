from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FlagCohortRollout(Base):
    """Percentage / cohort tag rollout for a feature flag (Phase C)."""

    __tablename__ = "flag_cohort_rollouts"
    __table_args__ = (UniqueConstraint("feature_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 0–100: deterministic hash(tenant.external_id) % 100 < percentage
    percentage: Mapped[int] = mapped_column(Integer, default=0)
    # Tenant.tags intersection — any match includes the tenant regardless of %
    cohort_tags: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
