import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from guard import SecurityConfig, SecurityMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.middleware_ops import ControlOpsMiddleware
from app.routers import (
    abuse,
    auth,
    billing,
    blocks,
    compliance,
    deals,
    dual_control,
    flags,
    financial,
    payment_settings,
    tutoring,
    incidents,
    internal,
    misc,
    monitoring,
    plans,
    providers,
    settings as settings_router,
    staff_users,
    support,
    tickets,
    tenants,
    usage,
)
from app.services.billing_worker import billing_worker_loop
from app.services.outbox_worker import outbox_worker_enabled, outbox_worker_loop
from app.services.seed import seed_all

cfg = get_settings()


def _configure_logging() -> None:
    if cfg.log_json:
        import json

        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                    "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                }
                if record.exc_info:
                    payload["exception"] = self.formatException(record.exc_info)
                return json.dumps(payload)

        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        )

    if cfg.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=cfg.sentry_dsn,
                environment=cfg.environment,
                traces_sample_rate=0.1,
                send_default_pii=False,
            )
            logging.getLogger("control").info("Sentry initialised for %s", cfg.environment)
        except Exception as exc:
            logging.getLogger("control").warning("Sentry init failed: %s", exc)


_configure_logging()
logger = logging.getLogger("control")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if cfg.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    else:
        logger.info("AUTO_CREATE_SCHEMA=false — expecting `alembic upgrade head` to have run")

    db = SessionLocal()
    try:
        result = seed_all(db, migrate_schema=cfg.auto_create_schema)
        logger.info("Seed complete: %s", result)
    finally:
        db.close()

    stop_event = asyncio.Event()
    worker_tasks: list[asyncio.Task] = []
    if not cfg.workers_enabled:
        logger.info("WORKERS_ENABLED=false — this replica will not run billing/outbox loops")
    else:
        if outbox_worker_enabled():
            worker_tasks.append(asyncio.create_task(outbox_worker_loop(stop_event)))
        if cfg.billing_worker_enabled:
            worker_tasks.append(asyncio.create_task(billing_worker_loop(stop_event)))

    yield

    stop_event.set()
    for worker_task in worker_tasks:
        # Bounded: a mid-flight batch must not hold shutdown open indefinitely.
        try:
            await asyncio.wait_for(worker_task, timeout=cfg.worker_shutdown_timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("Worker did not stop in time — cancelling")
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass


def _guard_config() -> SecurityConfig:
    """fastapi-guard sits outermost for bot/pen-test protection.

    Its rate_limit applies to *every* non-excluded path, so it must be sized for
    the busiest legitimate caller — the console dashboard fires several requests
    per page load. The per-budget limits in ControlOpsMiddleware do the real
    per-endpoint enforcement; this is a coarse abuse ceiling above them.
    """
    ceiling = max(
        cfg.rate_limit_staff_per_minute,
        cfg.rate_limit_m2m_per_minute,
        cfg.rate_limit_auth_per_minute,
    ) * 2
    return SecurityConfig(
        enable_redis=bool(cfg.redis_url),
        redis_url=cfg.redis_url or "redis://localhost:6379",
        rate_limit=ceiling,
        rate_limit_window=60,
        auto_ban_threshold=max(ceiling * 2, 50),
        auto_ban_duration=900,
        enable_cors=False,  # CORS handled by FastAPI middleware with allowlist
        # Caddy terminates public TLS; service-to-service traffic inside the
        # Docker network is intentionally plain HTTP.
        enforce_https=False,
        passive_mode=cfg.environment == "development",
        enable_penetration_detection=True,
        # Vercel injects JWTs / signatures into every function request. Guard's
        # semantic scanner scores those as "suspicious" and blocks real browsers
        # (incl. CORS preflight) unless we skip these platform headers.
        excluded_detection_headers={
            "x-vercel-oidc-token",
            "x-vercel-proxy-signature",
            "x-vercel-internal-intra-session",
            "forwarded",
        },
        exclude_paths=[
            "/docs",
            "/redoc",
            "/openapi.json",
            "/openapi.yaml",
            "/favicon.ico",
            "/static",
            "/health/live",
            "/health/ready",
        ],
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if cfg.environment == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def create_app() -> FastAPI:
    expose_docs = cfg.environment != "production"
    app = FastAPI(
        title=cfg.app_name,
        version="1.0.0",
        lifespan=lifespan,
        # The schema describes internal m2m routes including provider-secret
        # retrieval; it stays unpublished in production.
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    # Compress JSON responses — runtime-config payloads are large and fetched
    # frequently by every school replica.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ControlOpsMiddleware)
    # Last-added = outermost: Guard sees requests first (auth abuse / pen-test probes).
    app.add_middleware(SecurityMiddleware, config=_guard_config())
    # Keep CORS outermost so browser clients also receive CORS headers when an
    # inner middleware or route raises an unhandled exception.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(settings_router.router)
    app.include_router(staff_users.router)
    app.include_router(tenants.router)
    app.include_router(plans.router)
    app.include_router(flags.router)
    app.include_router(financial.router)
    app.include_router(payment_settings.router)
    app.include_router(tutoring.router)
    app.include_router(providers.router)
    app.include_router(blocks.router)
    app.include_router(billing.router)
    app.include_router(deals.router)
    app.include_router(usage.router)
    app.include_router(abuse.router)
    app.include_router(support.router)
    app.include_router(tickets.router)
    app.include_router(compliance.router)
    app.include_router(incidents.router)
    app.include_router(incidents.public_router)
    app.include_router(tickets.public_router)
    app.include_router(dual_control.router)
    app.include_router(monitoring.router)
    app.include_router(misc.router)
    app.include_router(internal.router)

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "requestId": request_id,
            },
        )

    @app.get("/health/live")
    def health_live():
        return {"status": "ok", "environment": cfg.environment}

    @app.get("/health/ready")
    def health_ready():
        from sqlalchemy import text

        checks = {"database": "ok", "redis": "skipped"}
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.error("Readiness probe failed: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": "database unreachable"},
            )
        if cfg.redis_url and cfg.environment != "development":
            try:
                import redis

                client = redis.from_url(cfg.redis_url, socket_connect_timeout=1)
                client.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                logger.error("Redis readiness probe failed: %s", exc)
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "reason": "redis unreachable"},
                )
        return {"status": "ready", "checks": checks}

    return app


app = create_app()
