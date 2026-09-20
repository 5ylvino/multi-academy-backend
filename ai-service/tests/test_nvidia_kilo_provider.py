from __future__ import annotations

import pytest
import httpx

from app.config import Settings
from app.providers.llm.base import LlmMessage
from app.providers.llm.nvidia_kilo import LlmUpstreamError, NvidiaKiloProvider, NVIDIA_NIM_BASE_URL


@pytest.mark.asyncio
async def test_prefers_nvidia_nim_when_nvapi_key_present(monkeypatch) -> None:
    settings = Settings(
        NVIDIA_API_KEY="nvapi-test-key",
        KILO_API_KEY="kilo-jwt-token",
        LLM_MODEL="nvidia/llama-3.1-nemotron-70b-instruct",
    )
    provider = NvidiaKiloProvider(settings, mode="nvidia")
    captured: dict[str, str] = {}

    async def fake_post(_self, url, **kwargs):
        captured["url"] = url
        captured["auth"] = kwargs["headers"]["Authorization"]
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hello"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await provider.chat(
        messages=[LlmMessage(role="user", content="Hi")],
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
    )

    assert result.content == "Hello"
    assert captured["url"] == f"{NVIDIA_NIM_BASE_URL}/chat/completions"
    assert captured["auth"] == "Bearer nvapi-test-key"


@pytest.mark.asyncio
async def test_retries_default_model_after_gateway_403(monkeypatch) -> None:
    settings = Settings(
        NVIDIA_API_KEY="nvapi-test-key",
        LLM_MODEL="nvidia/llama-3.1-nemotron-70b-instruct",
    )
    provider = NvidiaKiloProvider(settings, mode="nvidia")
    attempts: list[str] = []

    async def fake_post(_self, _url, **kwargs):
        model = kwargs["json"]["model"]
        attempts.append(model)
        request = httpx.Request("POST", _url)
        if model == "nvidia/unknown-model":
            return httpx.Response(403, text="forbidden", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Recovered"}}],
                "usage": {},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await provider.chat(
        messages=[LlmMessage(role="user", content="Hi")],
        model="nvidia/unknown-model",
    )

    assert result.content == "Recovered"
    assert attempts == [
        "nvidia/unknown-model",
        "nvidia/llama-3.1-nemotron-70b-instruct",
    ]


@pytest.mark.asyncio
async def test_kilo_mode_uses_kilo_gateway(monkeypatch) -> None:
    settings = Settings(
        NVIDIA_API_KEY="nvapi-test-key",
        KILO_API_KEY="kilo-jwt-token",
        KILO_BASE_URL="https://api.kilo.ai/api/gateway",
        LLM_MODEL="nvidia/llama-3.1-nemotron-70b-instruct",
    )
    provider = NvidiaKiloProvider(settings, mode="kilo")
    captured: dict[str, str] = {}

    async def fake_post(_self, url, **kwargs):
        captured["url"] = url
        captured["auth"] = kwargs["headers"]["Authorization"]
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Via Kilo"}}], "usage": {}},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await provider.chat(messages=[LlmMessage(role="user", content="Hi")])

    assert captured["url"] == "https://api.kilo.ai/api/gateway/chat/completions"
    assert captured["auth"] == "Bearer kilo-jwt-token"


def test_missing_keys_raises_clear_error() -> None:
    provider = NvidiaKiloProvider(Settings(NVIDIA_API_KEY="", KILO_API_KEY=""))
    with pytest.raises(LlmUpstreamError, match="not configured"):
        provider._resolve_endpoint()
