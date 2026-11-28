"""foundation identity tenancy platform

Revision ID: 0001
Revises:
Create Date: 2026-11-27 18:04:34.735710+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_realm", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=60), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_target", "audit_events", ["target_type", "target_id"], unique=False
    )
    op.create_index(
        "ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("realm", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.UUID(), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index("ix_auth_sessions_expires", "auth_sessions", ["expires_at"], unique=False)
    op.create_index("ix_auth_sessions_family", "auth_sessions", ["family_id"], unique=False)
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("default_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "rollout_percent", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_feature_flags")),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "sensitive_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.create_table(
        "plans",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "features",
            sa.ARRAY(sa.String(length=100)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("student_limit", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_plans")),
    )
    op.create_table(
        "platform_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_users")),
        sa.UniqueConstraint("email", name="uq_platform_users_email"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("plan_code", sa.String(length=40), nullable=False),
        sa.Column("school_levels", sa.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("contact_email", sa.String(length=254), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_index("ix_tenants_status_id", "tenants", ["status", "id"], unique=False)
    op.create_table(
        "school_users",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "capabilities",
            sa.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("primary_owner", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_school_users_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_school_users")),
        sa.UniqueConstraint("tenant_id", "email", name="uq_school_users_tenant_email"),
    )
    op.create_index("ix_school_users_id", "school_users", ["id"], unique=False)
    op.create_table(
        "tenant_flag_overrides",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("flag_key", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flag_key"],
            ["feature_flags.key"],
            name=op.f("fk_tenant_flag_overrides_flag_key_feature_flags"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_flag_overrides_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "flag_key", name=op.f("pk_tenant_flag_overrides")),
    )
    op.create_table(
        "email_verifications",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["school_users.tenant_id", "school_users.id"],
            name=op.f("fk_email_verifications_tenant_id_school_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_email_verifications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name=op.f("pk_email_verifications")),
    )
    op.create_index(
        "ix_email_verifications_lookup",
        "email_verifications",
        ["tenant_id", "user_id", "id"],
        unique=False,
    )

    _enable_row_level_security()
    _grant_app_role()
    _seed()


def downgrade() -> None:
    for table in TENANT_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_index("ix_email_verifications_lookup", table_name="email_verifications")
    op.drop_table("email_verifications")
    op.drop_table("tenant_flag_overrides")
    op.drop_index("ix_school_users_id", table_name="school_users")
    op.drop_table("school_users")
    op.drop_index("ix_tenants_status_id", table_name="tenants")
    op.drop_table("tenants")
    op.drop_table("platform_users")
    op.drop_table("plans")
    op.drop_index(
        "ix_outbox_events_pending",
        table_name="outbox_events",
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.drop_table("outbox_events")
    op.drop_table("feature_flags")
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_family", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_audit_events_tenant_created", table_name="audit_events")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS masms_current_tenant()")
    op.execute("DROP FUNCTION IF EXISTS masms_rls_bypass()")


TENANT_SCOPED = ("school_users", "email_verifications")
APP_ROLE = "masms_app"


def _enable_row_level_security() -> None:
    """Tenant isolation enforced by PostgreSQL itself.

    The app sets ``app.tenant_id`` with ``set_config(..., is_local => true)`` at the start of each
    transaction. FORCE makes the policy apply to the table owner too. Superusers still bypass RLS,
    so the application must connect as a non-superuser role.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION masms_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE AS
        $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION masms_rls_bypass() RETURNS boolean
        LANGUAGE sql STABLE PARALLEL SAFE AS
        $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'on' $$
        """
    )
    for table in TENANT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = masms_current_tenant() OR masms_rls_bypass())
            WITH CHECK (tenant_id = masms_current_tenant() OR masms_rls_bypass())
            """
        )


def _grant_app_role() -> None:
    """The runtime role gets data access only: no DDL, no ownership."""
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA public TO {APP_ROLE}';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}';
            EXECUTE 'REVOKE ALL ON alembic_version FROM {APP_ROLE}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}';
          END IF;
        END
        $$;
        """
    )


def _seed() -> None:
    op.execute(
        """
        INSERT INTO plans (code, name, features, student_limit, active)
        VALUES ('starter', 'Starter', '{}', 300, true)
        ON CONFLICT (code) DO NOTHING
        """
    )
