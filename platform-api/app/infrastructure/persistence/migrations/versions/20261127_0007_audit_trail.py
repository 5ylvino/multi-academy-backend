"""audit trail

Makes ``audit_events`` a tamper-resistant record of who changed what:

- Request details on every row: request id (matches the access log), client IP and device.
- Append-only for everyone: the app role keeps only SELECT and INSERT, and a trigger refuses
  UPDATE, DELETE and TRUNCATE even for the table owner. The only way to remove rows is the
  retention purge, which must set ``app.audit_purge = 'on'`` in its own transaction and may only
  delete rows older than the retention floor (7 years).
- Row-level security: a school sees only its own events; platform events (no tenant) are visible
  only to owner-console work, which runs with the RLS bypass.
- Indexes for the audit search: by school and time, by time, by actor, by target.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 21:10:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.add_column("audit_events", sa.Column("user_agent", sa.String(length=256), nullable=True))

    op.drop_index("ix_audit_events_tenant_created", table_name="audit_events")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.create_index(
        "ix_audit_events_tenant_time", "audit_events", ["tenant_id", "created_at", "id"]
    )
    op.create_index("ix_audit_events_time", "audit_events", ["created_at", "id"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor_id", "created_at"])
    op.create_index(
        "ix_audit_events_target", "audit_events", ["target_type", "target_id", "created_at"]
    )

    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    # Reads: a school's own rows, or everything for owner-console work.
    # Writes: a row for the school in scope, or a row with no school before one is known
    # (for example a failed sign-in to an unknown school).
    op.execute(
        """
        CREATE POLICY tenant_isolation ON audit_events
        USING (tenant_id = masms_current_tenant() OR masms_rls_bypass())
        WITH CHECK (
          masms_rls_bypass()
          OR tenant_id = masms_current_tenant()
          OR (tenant_id IS NULL AND masms_current_tenant() IS NULL)
        )
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE'
             AND coalesce(current_setting('app.audit_purge', true), '') = 'on'
             AND OLD.created_at < now() - interval '7 years' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_no_truncate() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is append-only (TRUNCATE refused)'
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_no_truncate()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'masms_app') THEN
            EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM masms_app';
            EXECUTE 'GRANT SELECT, INSERT ON audit_events TO masms_app';
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_no_truncate()")
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON audit_events")
    op.execute("ALTER TABLE audit_events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'masms_app') THEN
            EXECUTE 'GRANT UPDATE, DELETE ON audit_events TO masms_app';
          END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_time", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_time", table_name="audit_events")
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])
    op.create_index("ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at"])
    op.drop_column("audit_events", "user_agent")
    op.drop_column("audit_events", "ip_address")
    op.drop_column("audit_events", "request_id")
