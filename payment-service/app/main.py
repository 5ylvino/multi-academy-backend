from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.services.control_client import control_client

_INSECURE_JWT_SECRETS = frozenset({"change-me", "change-me-in-production"})


def _validate_production_settings(settings) -> None:
    if settings.app_env != "production":
        return
    if not control_client.is_configured():
        raise RuntimeError(
            "CONTROL_API_URL and CONTROL_M2M_CLIENT_* are required when APP_ENV=production"
        )
    if settings.service_jwt_secret in _INSECURE_JWT_SECRETS:
        raise RuntimeError("SERVICE_JWT_SECRET must be changed when APP_ENV=production")
    if settings.database_url.startswith(("sqlite", "postgresql+psycopg://localhost")):
        raise RuntimeError("DATABASE_URL must point to a managed production database")


def create_app() -> FastAPI:
    settings = get_settings()
    _validate_production_settings(settings)
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Payment gateway orchestration for Multi-Academy SMS.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-Tenant-Id"],
    )
    app.include_router(api_router)
    return app


app = create_app()
