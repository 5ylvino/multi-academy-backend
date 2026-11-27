"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from masms import __version__
from masms.infrastructure.config.settings import Settings, get_settings
from masms.infrastructure.container import Container, build_container
from masms.infrastructure.observability.logging import configure_logging
from masms.presentation.http.errors import install_error_handlers
from masms.presentation.http.middleware import RequestContextMiddleware
from masms.presentation.http.v1.router import api_router
from masms.presentation.http.v1.system.routes import router as system_router


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings, role="api")
        try:
            yield
        finally:
            await app.state.container.aclose()

    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    install_error_handlers(app)

    origins = sorted(set(settings.cors_school_origins) | set(settings.cors_platform_origins))
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
            expose_headers=["X-Request-Id", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(RequestContextMiddleware, metrics_enabled=settings.metrics_enabled)

    app.include_router(system_router)
    app.include_router(api_router, prefix="/api/v1")
    return app
