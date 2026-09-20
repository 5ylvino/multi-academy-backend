"""Shared pytest fixtures.

Each test gets its own SQLite file so suites cannot see each other's rows. The
env vars are set before any app module is imported, because `get_settings()` is
lru_cached and `app.config` is imported transitively by nearly everything.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="control-tests-"))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'base.db'}"
os.environ["JWT_SECRET"] = "test-secret-key-at-least-32-characters-long!"
os.environ["SECRETS_ENCRYPTION_KEY"] = ""
os.environ["REDIS_URL"] = ""
os.environ["NEST_CONFIG_WEBHOOK_URL"] = ""
os.environ["IP_ALLOWLIST_ENFORCED"] = "false"
os.environ["TRUSTED_PROXY_COUNT"] = "0"
os.environ["DUAL_CONTROL_REQUIRED"] = "false"
os.environ["BILLING_WORKER_ENABLED"] = "false"
os.environ["WORKERS_ENABLED"] = "false"
# Keep the middleware's per-minute budgets out of the way of test volume.
os.environ["RATE_LIMIT_STAFF_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_AUTH_PER_MINUTE"] = "100000"
os.environ["RATE_LIMIT_M2M_PER_MINUTE"] = "100000"


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / f'{uuid.uuid4().hex}.db'}"


@pytest.fixture
def session(db_url):
    """A SQLAlchemy session against an isolated schema built from the models."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    import app.models  # noqa: F401  (registers every table)

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def tenant(session):
    from app.models import Tenant

    t = Tenant(
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        slug=f"school-{uuid.uuid4().hex[:8]}",
        name="Test Academy",
        status="active",
        admin_email="admin@testacademy.ng",
    )
    session.add(t)
    session.commit()
    return t


@pytest.fixture
def subscription(session, tenant):
    from datetime import datetime, timezone

    from app.models import Subscription

    sub = Subscription(
        tenant_id=tenant.id,
        type="saas",
        status="active",
        billing_cycle="monthly",
        price_minor=500_000,
        currency="NGN",
        is_current=True,
        approval_status="active",
        pricing_model="flat",
        next_invoice_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )
    session.add(sub)
    session.commit()
    return sub
