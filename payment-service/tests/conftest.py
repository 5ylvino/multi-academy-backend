import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SERVICE_JWT_SECRET", "test-secret")
os.environ.setdefault("SERVICE_JWT_AUDIENCE", "mas-payment-service")
os.environ.setdefault("SERVICE_JWT_ISSUER", "mas-school-server")
os.environ["CONTROL_API_URL"] = ""
os.environ["CONTROL_M2M_CLIENT_ID"] = ""
os.environ["CONTROL_M2M_CLIENT_SECRET"] = ""

from app.db.models import Base
from app.db.session import get_db
from app.main import create_app
from app.security.service_jwt import issue_service_token


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    app = create_app()

    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers():
    def _build(features: list[str] | None = None, roles: list[str] | None = None):
        token = issue_service_token(
            tenant_id="tenant-demo",
            actor_id="user-parent-1",
            roles=roles or ["parent"],
            features=features or ["fees.gateway"],
        )
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-demo",
        }

    return _build
