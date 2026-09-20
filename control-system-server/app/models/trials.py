from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TenantInstall(Base):
    """Credential fingerprint for the school runtime installation."""

    __tablename__ = "tenant_installs"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_installs_tenant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    install_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    token_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantTrial(Base):
    """One immutable trial grant with operator-adjustable controls."""

    __tablename__ = "tenant_trials"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_trials_tenant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=90)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reminder_7_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_1_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
