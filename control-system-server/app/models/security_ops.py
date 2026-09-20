"""Staff sessions, IP allowlist, MFA policy, and tenant offboarding stubs."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StaffSession(Base):
    """Revocable staff session bound to JWT jti (access + refresh pair)."""

    __tablename__ = "staff_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("platform_users.id"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(String(255), default="")


class IpAllowlistEntry(Base):
    """CIDR or exact IP allowed to reach control staff API (when enforced)."""

    __tablename__ = "ip_allowlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidr: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecuritySettings(Base):
    """Single-row org security policy for platform staff."""

    __tablename__ = "security_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_allowlist_enforced: Mapped[bool] = mapped_column(Boolean, default=False)
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, default=30)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantOffboardJob(Base):
    """Stub offboarding workflow: export → wipe schedule → legal-hold check."""

    __tablename__ = "tenant_offboard_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    # scheduled | export_pending | wipe_scheduled | blocked_legal_hold | completed | cancelled
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    export_package_uri: Mapped[str] = mapped_column(String(512), default="")
    wipe_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legal_hold_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
