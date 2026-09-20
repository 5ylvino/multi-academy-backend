from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings

_INSECURE_JWT_SECRETS = frozenset({"change-me", "change-me-in-production"})


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.app_env == "production" and settings.service_jwt_secret in _INSECURE_JWT_SECRETS:
        raise RuntimeError("SERVICE_JWT_SECRET must be changed when APP_ENV=production")
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Read-optimized portal API with Redis cache.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-Tenant-Id"],
    )
    app.include_router(api_router)
    return app


app = create_app()
