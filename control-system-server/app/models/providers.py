from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CAPABILITIES = (
    "payment",
    "sms",
    "email",
    "ai",
    "meeting",
    "storage",
    "push",
    "maps",
    "oauth",
)


class ProviderConfig(Base):
    """Active provider for a capability, globally or per tenant (tenant override)."""

    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("capability", "tenant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    # NULL tenant_id = global default; a row with tenant_id overrides for that tenant.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    provider_id: Mapped[str] = mapped_column(String(64))  # paystack | africas_talking | google_meet | ...
    mode: Mapped[str] = mapped_column(String(16), default="sandbox")  # sandbox | live
    # Non-secret settings: sender id, callback URL, model name, temperature, spend caps...
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")  # green|amber|red|unknown
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    secrets: Mapped[list["ProviderSecret"]] = relationship(back_populates="config")


class ProviderSecret(Base):
    """Envelope-encrypted credential attached to a provider config, versioned."""

    __tablename__ = "provider_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("provider_configs.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))  # e.g. secret_key, public_key, api_key
    ciphertext: Mapped[str] = mapped_column(String(2048))
    fingerprint: Mapped[str] = mapped_column(String(64))  # display hint only
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    config: Mapped[ProviderConfig] = relationship(back_populates="secrets")


class ProviderFailoverPolicy(Base):
    """Circuit-breaker / secondary provider policy per capability (Phase C)."""

    __tablename__ = "provider_failover_policies"
    __table_args__ = (UniqueConstraint("capability", "tenant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    # NULL = global policy; tenant_id overrides for that school.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    primary_provider_id: Mapped[str] = mapped_column(String(64))
    secondary_provider_id: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Trip when consecutive failures / red health hits threshold (ops signal).
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    # When tripped, Nest/runtime uses secondary until cleared or cooldown ends.
    is_tripped: Mapped[bool] = mapped_column(Boolean, default=False)
    tripped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
