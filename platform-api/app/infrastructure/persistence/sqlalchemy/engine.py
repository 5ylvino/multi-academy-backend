"""Engine and session factory construction."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.config.settings import Settings


def create_engine(url: str, settings: Settings, *, application_name: str) -> AsyncEngine:
    connect_args: dict[str, Any] = {
        "server_settings": {
            "application_name": application_name,
            "statement_timeout": str(settings.database_statement_timeout_ms),
            "idle_in_transaction_session_timeout": "30000",
            "jit": "off",
        },
        "command_timeout": settings.database_statement_timeout_ms / 1000 + 5,
    }
    if settings.database_pgbouncer:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
    return create_async_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
