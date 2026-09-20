from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base


def _sqlalchemy_url(url: str) -> str:
    for bare in ("postgresql://", "postgres://"):
        if url.startswith(bare):
            return "postgresql+psycopg://" + url[len(bare) :]
    return url


settings = get_settings()
database_url = _sqlalchemy_url(settings.database_url)

engine_kwargs: dict = {"pool_pre_ping": True}
if database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def init_db() -> None:
    """Create tables when migrations have not been applied (dev convenience only)."""
    Base.metadata.create_all(bind=engine)
