from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

TENANT_STATUSES = (
    "pending_verification",
    "active",
    "suspended",
    "restricted",
    "blacklisted",
    "provisioning",
    "archived",
)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # External id used by the school Nest server (its organization/tenant id).
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    status_message: Mapped[str] = mapped_column(Text, default="")
    # Machine-readable cause of a non-active status: billing_dunning | abuse |
    # compliance | manual | offboarding. Auto-reactivation keys off this instead
    # of substring-matching the human-readable status_message.
    status_reason: Mapped[str] = mapped_column(String(32), default="", index=True)
    region: Mapped[str] = mapped_column(String(32), default="NG")
    # NDPR residency tag — where tenant data is expected to reside (e.g. NG, EU).
    residency_tag: Mapped[str] = mapped_column(String(32), default="NG")
    isolation_mode: Mapped[str] = mapped_column(String(32), default="db-per-tenant")
    # Legal hold blocks DSR delete / wipe workflows.
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_hold_reason: Mapped[str] = mapped_column(Text, default="")

    admin_email: Mapped[str] = mapped_column(String(255), default="")
    admin_phone: Mapped[str] = mapped_column(String(64), default="")

    tags: Mapped[list] = mapped_column(JSON, default=list)  # VIP, trial, at-risk, cohort-beta...
    notes: Mapped[str] = mapped_column(Text, default="")
    # Temporary billing unlock (courtesy) — tenant stays active until this time.
    courtesy_unlock_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantProvisioningJob(Base):
    """Control-plane record for school-runtime initialization.

    The control plane owns the request and its lifecycle, but never treats a
    queued event as proof that the school's database was created.
    """

    __tablename__ = "tenant_provisioning_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    school_db_status: Mapped[str] = mapped_column(String(32), default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(128), default="")
    defaults_version: Mapped[str] = mapped_column(String(32), default="v1")
    requested_by: Mapped[str] = mapped_column(String(255), default="")
    requested_by_id: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TenantDomain(Base):
    """Control-plane-owned hostnames used to reach a school runtime."""

    __tablename__ = "tenant_domains"
    __table_args__ = (UniqueConstraint("hostname", name="uq_tenant_domains_hostname"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    hostname: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="subdomain")  # subdomain | custom
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | disabled
    ssl_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | failed
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SchoolBlacklistEntry(Base):
    """Identity fingerprints preventing a blacklisted school from re-registering."""

    __tablename__ = "school_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    school_name_normalized: Mapped[str] = mapped_column(String(255), default="", index=True)
    slug: Mapped[str] = mapped_column(String(128), default="", index=True)
    admin_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    email_domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(64), default="", index=True)
    registration_id: Mapped[str] = mapped_column(String(128), default="")  # CAC / ministry id
    ip_ranges: Mapped[list] = mapped_column(JSON, default=list)

    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="high")
    permanent: Mapped[bool] = mapped_column(Boolean, default=True)
    review_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
