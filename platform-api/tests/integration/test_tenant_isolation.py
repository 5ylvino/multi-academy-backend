"""Every tenant-scoped table is isolated by PostgreSQL RLS, and none is forgotten."""

from __future__ import annotations

import asyncpg
import pytest

from masms.infrastructure.persistence.models import academics as _academics  # noqa: F401
from masms.infrastructure.persistence.sqlalchemy.base import (
    TENANT_REGISTRY_TABLES,
    TENANT_SCOPED_TABLES,
    Base,
)

pytestmark = pytest.mark.integration


def test_every_tenant_table_is_classified() -> None:
    with_tenant = {t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c}
    unclassified = with_tenant - set(TENANT_SCOPED_TABLES) - set(TENANT_REGISTRY_TABLES)
    assert not unclassified, f"classify these tables in base.py: {sorted(unclassified)}"


async def test_scoped_tables_force_rls_with_policy(migrated: None, owner_dsn: str) -> None:
    conn = await asyncpg.connect(owner_dsn)
    try:
        rows = await conn.fetch(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname "
            "AND p.policyname = 'tenant_isolation') AS has_policy "
            "FROM pg_class c WHERE c.relname = ANY($1::text[]) AND c.relkind = 'r'",
            list(TENANT_SCOPED_TABLES),
        )
    finally:
        await conn.close()
    found = {r["relname"]: r for r in rows}
    assert set(found) == set(TENANT_SCOPED_TABLES)
    for name, row in found.items():
        assert row["relrowsecurity"], name
        assert row["relforcerowsecurity"], name
        assert row["has_policy"], name
