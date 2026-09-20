from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _sqlalchemy_url(url: str) -> str:
    """Map bare postgres URLs onto psycopg v3 (what requirements.txt ships).

    Neon / Vercel paste links are usually ``postgresql://...``. SQLAlchemy
    treats that as the legacy psycopg2 driver and ImportErrors if it is not
    installed. Explicit ``+psycopg`` / ``+asyncpg`` dialects are left alone.
    """
    for bare in ("postgresql://", "postgres://"):
        if url.startswith(bare):
            return "postgresql+psycopg://" + url[len(bare) :]
    return url


def is_neon_pooler(url: str) -> bool:
    """Neon PgBouncer hosts include ``-pooler.`` and reject startup ``options``."""
    return "-pooler." in url


def build_engine_kwargs(url: str) -> dict:
    """Connection-pool settings for Neon/Postgres. SQLite stays single-threaded.

    Do not pass ``statement_timeout`` via libpq ``options`` — Neon pooled
    endpoints reject it as an unsupported startup parameter. The timeout is
    applied after connect instead (see ``attach_statement_timeout``).
    """
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        return kwargs
    kwargs.update(
        pool_size=max(1, settings.db_pool_size),
        max_overflow=max(0, settings.db_max_overflow),
        pool_recycle=max(60, settings.db_pool_recycle_seconds),
        pool_timeout=max(1, settings.db_pool_timeout_seconds),
    )
    return kwargs


def attach_statement_timeout(target: Engine) -> None:
    """SET statement_timeout on each Postgres checkout. Safe on Neon pooler."""
    if target.dialect.name == "sqlite":
        return
    timeout_ms = max(1000, int(settings.db_statement_timeout_ms))

    @event.listens_for(target, "connect")
    def _set_statement_timeout(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET statement_timeout TO {timeout_ms}")
        finally:
            cursor.close()


database_url = _sqlalchemy_url(settings.database_url)

engine = create_engine(database_url, **build_engine_kwargs(database_url))
attach_statement_timeout(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
