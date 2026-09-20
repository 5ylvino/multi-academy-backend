from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Platform roles are fixed (see CONTROL-SYSTEM-BUILD.md §4); permissions per
# role are resolved in app.rbac. Role name is stored on the user.
PLATFORM_ROLES = (
    "owner",
    "ops_admin",
    "billing_admin",
    "support_agent",
    "security_analyst",
    "auditor",
    "engineer_staging",
)


class PlatformUser(Base):
    __tablename__ = "platform_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="auditor")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ServiceClient(Base):
    """m2m credential for a school Nest deployment/environment."""

    __tablename__ = "service_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    secret_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # runtime-config:read, usage:write, ...
    environment: Mapped[str] = mapped_column(String(32), default="development")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional tenant scoping. Empty list = every tenant, which is the normal
    # case: one school server deployment serves all schools. Populate it to pin
    # a client to specific tenants (dedicated or staging deployments).
    allowed_tenant_refs: Mapped[list] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="staff")  # staff | service | system
    actor_id: Mapped[str] = mapped_column(String(64), default="")
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(128), index=True)  # e.g. flag.override.set
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SystemState(Base):
    """Single-row table carrying the global runtime configVersion."""

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
