from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()
database_url = settings.database_url
if database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
elif database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://") :]

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
