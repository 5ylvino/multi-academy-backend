from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.config import get_settings
from app.deps import get_llm_registry, get_pinecone_client, get_storage_client
from app.providers.llm.nvidia_kilo import LlmUpstreamError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    # Warm singletons on startup.
    get_llm_registry()
    get_pinecone_client()
    get_storage_client()
    yield
    # Release cached singletons.
    get_llm_registry.cache_clear()
    get_pinecone_client.cache_clear()
    get_storage_client.cache_clear()
    get_settings.cache_clear()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Agentic RAG microservice for Multi-Academy SMS. "
            "Called by the NestJS school server; never exposed directly to browsers."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-Tenant-Id"],
    )
    @app.exception_handler(LlmUpstreamError)
    async def llm_upstream_error_handler(_request: Request, exc: LlmUpstreamError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "detail": str(exc),
                "model": exc.model,
                "upstreamStatus": exc.status_code,
            },
        )

    app.include_router(api_router)
    return app


app = create_app()
