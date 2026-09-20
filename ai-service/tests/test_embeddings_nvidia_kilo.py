from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.providers.embeddings.nvidia_kilo import NVIDIA_NIM_BASE_URL, NvidiaKiloEmbeddings


@pytest.mark.asyncio
async def test_embeddings_prefers_nvidia_nim_for_nvapi_key(monkeypatch) -> None:
    embedder = NvidiaKiloEmbeddings(
        Settings(
            NVIDIA_API_KEY="nvapi-test-key",
            KILO_API_KEY="kilo-jwt",
            EMBEDDING_MODEL="nvidia/nemotron-3-embed-1b",
        )
    )
    captured: dict[str, str] = {}

    async def fake_post(_self, url, **kwargs):
        captured["url"] = url
        captured["auth"] = kwargs["headers"]["Authorization"]
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    vectors = await embedder.embed(["hello"])

    assert vectors == [[0.1, 0.2]]
    assert captured["url"] == f"{NVIDIA_NIM_BASE_URL}/embeddings"
    assert captured["auth"] == "Bearer nvapi-test-key"


@pytest.mark.asyncio
async def test_embeddings_use_kilo_when_only_kilo_key(monkeypatch) -> None:
    embedder = NvidiaKiloEmbeddings(
        Settings(
            NVIDIA_API_KEY="",
            KILO_API_KEY="kilo-jwt",
            KILO_BASE_URL="https://api.kilo.ai/api/gateway",
            EMBEDDING_MODEL="nvidia/nemotron-3-embed-1b",
        )
    )
    captured: dict[str, str] = {}

    async def fake_post(_self, url, **kwargs):
        captured["url"] = url
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0]}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await embedder.embed(["hello"])

    assert captured["url"] == "https://api.kilo.ai/api/gateway/embeddings"
