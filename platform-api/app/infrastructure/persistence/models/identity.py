"""Identity tables. School users are tenant-scoped (RLS); platform users are not."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class _UserColumns:
    email: Mapped[str] = mapped_column(String(254))
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    status: Mapped[str] = mapped_column(String(16))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class SchoolUserRow(_UserColumns, Base):
    __tablename__ = "school_users"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(64)), server_default=text("'{}'"))
    primary_owner: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "email", name="uq_school_users_tenant_email"),
        Index("ix_school_users_id", "id"),
        Index("ix_school_users_roles", "roles", postgresql_using="gin"),
    )


class PlatformUserRow(_UserColumns, Base):
    __tablename__ = "platform_users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)

    __table_args__ = (UniqueConstraint("email", name="uq_platform_users_email"),)


class PlatformMfaFactorRow(Base):
    """Authenticator-app factor for owner-console staff. The seed is AES-GCM encrypted."""

    __tablename__ = "platform_mfa_factors"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), unique=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'")
    )

    __table_args__ = (ForeignKeyConstraint(["user_id"], ["platform_users.id"], ondelete="CASCADE"),)


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    family_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    realm: Mapped[str] = mapped_column(String(16))
    tenant_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip_address: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        Index("ix_auth_sessions_family", "family_id"),
        Index("ix_auth_sessions_user", "user_id"),
        Index("ix_auth_sessions_expires", "expires_at"),
    )


class UserInviteRow(Base):
    __tablename__ = "user_invites"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "token_hash", name="uq_user_invites_tenant_token"),
    )


class GuardianLinkRow(Base):
    """Parent to student. Tenant-scoped with RLS."""

    __tablename__ = "school_guardian_links"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    parent_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    relationship: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
            name="fk_school_guardian_links_parent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="CASCADE",
            name="fk_school_guardian_links_student",
        ),
        Index("ix_school_guardian_links_student", "tenant_id", "student_id"),
    )
