from __future__ import annotations

from dataclasses import asdict, dataclass

from app.config import Settings, get_settings
from app.db.session import ping_database
from app.deps import get_llm_registry, get_pinecone_client, get_storage_client
from app.providers.llm.base import LlmMessage


@dataclass
class DependencyStatus:
    name: str
    ok: bool
    message: str


@dataclass
class ReadyReport:
    status: str
    checks: list[DependencyStatus]

    def to_dict(self) -> dict:
        return {"status": self.status, "checks": [asdict(c) for c in self.checks]}


def _llm_configured(settings: Settings) -> bool:
    return bool(settings.kilo_api_key.strip() or settings.nvidia_api_key.strip())


async def build_ready_report(settings: Settings | None = None) -> ReadyReport:
    cfg = settings or get_settings()
    checks: list[DependencyStatus] = []

    db_ok = ping_database()
    checks.append(
        DependencyStatus(
            name="database",
            ok=db_ok,
            message="Neon Postgres reachable" if db_ok else "Database ping failed",
        )
    )

    pinecone = get_pinecone_client()
    pine_health = pinecone.health_check()
    checks.append(
        DependencyStatus(
            name="pinecone",
            ok=pine_health.ok,
            message=pine_health.message,
        )
    )

    storage = get_storage_client()
    storage_health = storage.health_check()
    checks.append(
        DependencyStatus(
            name="object_storage",
            ok=storage_health.ok,
            message=storage_health.message,
        )
    )

    llm_ok = _llm_configured(cfg)
    llm_message = "NVIDIA/Kilo API key configured" if llm_ok else "LLM API key missing"
    checks.append(DependencyStatus(name="llm", ok=llm_ok, message=llm_message))

    all_ok = all(c.ok for c in checks)
    return ReadyReport(status="ok" if all_ok else "degraded", checks=checks)


async def ping_llm(settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    if not _llm_configured(cfg):
        raise RuntimeError("LLM provider is not configured")
    registry = get_llm_registry()
    provider = registry.resolve(cfg.llm_provider)
    response = await provider.chat(
        messages=[
            LlmMessage(role="system", content="Reply with exactly: pong"),
            LlmMessage(role="user", content="ping"),
        ],
        model=cfg.llm_model,
        max_tokens=16,
        temperature=0,
    )
    return {
        "providerId": response.provider_id,
        "model": response.model,
        "content": response.content.strip(),
    }
