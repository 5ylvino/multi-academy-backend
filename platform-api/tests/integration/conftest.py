"""Integration fixtures: real PostgreSQL (with RLS, as a non-superuser app role) and real Redis.

Environment:
  TEST_DATABASE_URL            app role, e.g. postgresql+asyncpg://masms_app:app@localhost:5432/masms_test
  TEST_MIGRATION_DATABASE_URL  owner role used for migrations and cleanup
  TEST_REDIS_URL               e.g. redis://localhost:6379/15
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from app.infrastructure.config.settings import Environment, Settings
from app.infrastructure.container import Container, build_container
from app.infrastructure.messaging.outbox_relay import OutboxRelay
from app.presentation.http.app import create_app

APP_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_URL = os.environ.get("TEST_MIGRATION_DATABASE_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")

pytestmark = pytest.mark.integration

if not (APP_URL and OWNER_URL and REDIS_URL):
    pytest.skip("integration settings not provided", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session")
def migrated() -> None:
    env = {**os.environ, "MASMS_MIGRATION_DATABASE_URL": OWNER_URL or ""}
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(  # noqa: S603 - fixed arguments
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )


class Mailbox:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def handler(self, name: str):  # type: ignore[no-untyped-def]
        async def _h(payload: Mapping[str, Any]) -> None:
            self.events.append((name, dict(payload)))

        return _h

    def last_code(self) -> str:
        for name, payload in reversed(self.events):
            if name == "tenancy.verification_code_issued":
                return str(payload["code"])
        raise AssertionError("no verification code was sent")

    def last_invite_token(self, email: str) -> str:
        for name, payload in reversed(self.events):
            if name == "identity.user_invited" and payload["email"] == email:
                return str(payload["token"])
        raise AssertionError(f"no invite was sent to {email}")


@pytest.fixture
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        object_store="local",
        object_store_path=str(tmp_path_factory.mktemp("objects")),
        environment=Environment.TEST,
        database_url=APP_URL,  # type: ignore[arg-type]
        redis_url=REDIS_URL,  # type: ignore[arg-type]
        log_json=False,
        log_level="WARNING",
        trusted_proxy_hops=0,
    )


@pytest.fixture
async def container(migrated: None, settings: Settings) -> AsyncIterator[Container]:
    conn = await asyncpg.connect(_plain(OWNER_URL or ""))
    try:
        tables = [
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename NOT IN ('alembic_version','plans')"
            )
        ]
        # The audit trail refuses TRUNCATE; only the table owner can lift that, as here, between
        # tests. Production never runs with the owner role.
        async with conn.transaction():
            await conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_truncate")
            await conn.execute(f"TRUNCATE {', '.join(tables)} CASCADE")
            await conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_truncate")
        await conn.execute("DELETE FROM plans WHERE code <> 'starter'")
    finally:
        await conn.close()
    redis = Redis.from_url(REDIS_URL or "")
    await redis.flushdb()
    await redis.aclose()

    c = build_container(settings)
    yield c
    await c.aclose()


@pytest.fixture
def mailbox(container: Container) -> Mailbox:
    box = Mailbox()
    container.outbox = OutboxRelay(
        container.session_factory,
        {
            name: [box.handler(name)]
            for name in ("tenancy.verification_code_issued", "identity.user_invited")
        },
    )
    return box


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container.settings, container=container)
    app.state.container = container
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def owner_dsn() -> str:
    return _plain(OWNER_URL or "")


@pytest.fixture
def app_dsn() -> str:
    return _plain(APP_URL or "")
