"""Alembic environment. Migrations run with the migration role (MASMS_MIGRATION_DATABASE_URL),
which owns the tables; the app role only gets DML grants."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from masms.infrastructure.config.settings import get_settings
from masms.infrastructure.persistence import models  # noqa: F401  (registers tables)
from masms.infrastructure.persistence.sqlalchemy.base import Base

target_metadata = Base.metadata


def _url() -> str:
    return (
        os.environ.get("MASMS_MIGRATION_DATABASE_URL")
        or get_settings().database_url.get_secret_value()
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
