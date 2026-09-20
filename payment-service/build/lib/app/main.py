from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
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
