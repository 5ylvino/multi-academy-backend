from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.providers.pinecone.client import PineconeHealth
from app.providers.storage.s3 import StorageHealth
from app.services.health import DependencyStatus, ReadyReport

client = TestClient(app)


def test_ready_returns_503_when_degraded() -> None:
    report = ReadyReport(
        status="degraded",
        checks=[
            DependencyStatus(name="database", ok=False, message="down"),
            DependencyStatus(name="pinecone", ok=False, message="missing key"),
            DependencyStatus(name="object_storage", ok=False, message="missing creds"),
            DependencyStatus(name="llm", ok=False, message="missing key"),
        ],
    )
    with patch("app.api.health.build_ready_report", new=AsyncMock(return_value=report)):
        res = client.get("/health/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "degraded"
    assert len(body["checks"]) == 4


def test_ready_returns_200_when_ok() -> None:
    report = ReadyReport(
        status="ok",
        checks=[
            DependencyStatus(name="database", ok=True, message="ok"),
            DependencyStatus(name="pinecone", ok=True, message="ok"),
            DependencyStatus(name="object_storage", ok=True, message="ok"),
            DependencyStatus(name="llm", ok=True, message="ok"),
        ],
    )
    with patch("app.api.health.build_ready_report", new=AsyncMock(return_value=report)):
        res = client.get("/health/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_pinecone_not_configured_reports_degraded(monkeypatch) -> None:
    from app.config import Settings
    from app.providers.pinecone.client import PineconeClient

    settings = Settings(PINECONE_API_KEY="")
    monkeypatch.setenv("PINECONE_API_KEY", "")
    health = PineconeClient(settings).health_check()
    assert health.ok is False
    assert "PINECONE_API_KEY" in health.message


def test_storage_not_configured_reports_degraded(monkeypatch) -> None:
    from app.config import Settings
    from app.providers.storage.s3 import ObjectStorageClient

    settings = Settings(
        OBJECT_STORAGE_ACCESS_KEY="",
        OBJECT_STORAGE_SECRET_KEY="",
        OBJECT_STORAGE_BUCKET="mas-ai-documents",
    )
    health = ObjectStorageClient(settings).health_check()
    assert health.ok is False
