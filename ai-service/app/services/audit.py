from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.db.models import AiAuditEvent
from app.db.session import SessionLocal
from app.schemas.common import SourceCitation, UsageInfo
from app.security.context import RequestContext


@contextmanager
def audit_timer() -> Iterator[dict[str, int]]:
    started = time.perf_counter()
    payload: dict[str, int] = {"latency_ms": 0}
    try:
        yield payload
    finally:
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)


def write_audit_event(
    *,
    ctx: RequestContext,
    feature: str,
    provider_id: str | None,
    model: str | None,
    usage: UsageInfo | None,
    sources: list[SourceCitation] | None,
    status: str = "ok",
    error_message: str | None = None,
    latency_ms: int | None = None,
    db: Session | None = None,
) -> None:
    session = db or SessionLocal()
    owns_session = db is None
    try:
        session.add(
            AiAuditEvent(
                id=uuid.uuid4(),
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                request_id=ctx.request_id,
                feature=feature,
                provider_id=provider_id,
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                latency_ms=latency_ms,
                sources_json=[s.model_dump(by_alias=True) for s in (sources or [])],
                status=status,
                error_message=error_message,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        if owns_session:
            session.close()
        return
    finally:
        if owns_session:
            session.close()
