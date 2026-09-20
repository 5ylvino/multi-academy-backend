import os

import pytest

# Test defaults — no external services required for unit tests.
os.environ.setdefault("SERVICE_JWT_SECRET", "test-secret")
os.environ.setdefault("SERVICE_JWT_AUDIENCE", "mas-ai-service")
os.environ.setdefault("SERVICE_JWT_ISSUER", "mas-school-server")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("LLM_PROVIDER", "nvidia")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings
    from app.deps import get_llm_registry, get_pinecone_client, get_storage_client

    get_settings.cache_clear()
    get_llm_registry.cache_clear()
    get_pinecone_client.cache_clear()
    get_storage_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_llm_registry.cache_clear()
    get_pinecone_client.cache_clear()
    get_storage_client.cache_clear()
