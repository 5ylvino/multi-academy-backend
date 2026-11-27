"""Liveness, readiness and metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from masms.infrastructure.container import Container
from masms.infrastructure.observability import metrics
from masms.presentation.http.dependencies.context import get_container

router = APIRouter(tags=["system"])


@router.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def ready(container: Annotated[Container, Depends(get_container)]) -> JSONResponse:
    checks = await container.check_ready()
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus(container: Annotated[Container, Depends(get_container)]) -> Response:
    if not container.settings.metrics_enabled:
        return Response(status_code=404)
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)
