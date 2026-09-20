from app.database import build_engine_kwargs, is_neon_pooler


def test_sqlite_skips_pool_tuning():
    kwargs = build_engine_kwargs("sqlite:///./control.db")
    assert "pool_size" not in kwargs
    assert kwargs["connect_args"]["check_same_thread"] is False


def test_postgres_gets_a_bounded_pool_without_startup_options():
    kwargs = build_engine_kwargs("postgresql+psycopg://u:p@h/db")
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_size"] >= 1
    assert kwargs["max_overflow"] >= 0
    assert "connect_args" not in kwargs


def test_neon_pooler_host_is_detected():
    assert is_neon_pooler(
        "postgresql+psycopg://u:p@ep-x-pooler.c-5.us-east-1.aws.neon.tech/db"
    )
    assert not is_neon_pooler(
        "postgresql+psycopg://u:p@ep-x.us-east-1.aws.neon.tech/db"
    )
