"""NVIDIA NIM and Kilo AI Gateway (OpenAI-compatible chat completions)."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.providers.llm.base import LlmMessage, LlmProvider, LlmResponse, LlmUsage

logger = logging.getLogger(__name__)

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Control-plane / Kilo-only ids that are not accepted on every gateway account.
MODEL_ALIASES: dict[str, str] = {
    "nvidia/nemotron-3-ultra-550b-a55b:free": "nvidia/llama-3.1-nemotron-70b-instruct",
}


class LlmUpstreamError(RuntimeError):
    """LLM vendor rejected or failed the request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.model = model
        self.base_url = base_url


class NvidiaKiloProvider(LlmProvider):
    id = "nvidia"

    def __init__(self, settings: Settings, *, mode: str = "nvidia") -> None:
        self._settings = settings
        self._mode = mode.strip().lower()
        self._default_model = settings.llm_model

    def _resolve_endpoint(self) -> tuple[str, str]:
        """Return (base_url, api_key) for the configured backend."""
        kilo_key = (self._settings.kilo_api_key or "").strip()
        nvidia_key = (self._settings.nvidia_api_key or "").strip()

        if self._mode == "kilo" and kilo_key:
            return self._settings.kilo_base_url.rstrip("/"), kilo_key

        # Prefer NVIDIA NIM when an nvapi- key is present (build.nvidia.com catalog).
        if nvidia_key.startswith("nvapi-"):
            return NVIDIA_NIM_BASE_URL, nvidia_key

        if kilo_key:
            return self._settings.kilo_base_url.rstrip("/"), kilo_key

        if nvidia_key:
            return NVIDIA_NIM_BASE_URL, nvidia_key

        raise LlmUpstreamError(
            "NVIDIA/Kilo API key is not configured. Set NVIDIA_API_KEY (nvapi-…) "
            "or KILO_API_KEY in the AI service environment."
        )

    def _models_to_try(self, model: str | None) -> list[str]:
        requested = (model or self._default_model).strip()
        normalized = MODEL_ALIASES.get(requested, requested)
        candidates = [normalized]
        if self._default_model and self._default_model not in candidates:
            candidates.append(self._default_model)
        # De-dupe while preserving order.
        seen: set[str] = set()
        ordered: list[str] = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    async def _chat_once(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[LlmMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> LlmResponse:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature
            if temperature is not None
            else self._settings.llm_temperature,
            "max_tokens": max_tokens
            if max_tokens is not None
            else self._settings.llm_max_tokens,
        }
        timeout = self._settings.request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if res.status_code >= 400:
                detail = res.text[:300]
                raise LlmUpstreamError(
                    f"LLM request failed ({res.status_code}) for model {model}: {detail}",
                    status_code=res.status_code,
                    model=model,
                    base_url=base_url,
                )
            data = res.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage_raw = data.get("usage") or {}
        return LlmResponse(
            content=content,
            model=model,
            provider_id=self.id,
            usage=LlmUsage(
                prompt_tokens=usage_raw.get("prompt_tokens"),
                completion_tokens=usage_raw.get("completion_tokens"),
            ),
            raw=data,
        )

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tenant_id: str | None = None,
    ) -> LlmResponse:
        del tenant_id  # reserved for future per-tenant vault routing
        base_url, api_key = self._resolve_endpoint()
        last_error: LlmUpstreamError | None = None

        for attempt_model in self._models_to_try(model):
            try:
                return await self._chat_once(
                    base_url=base_url,
                    api_key=api_key,
                    model=attempt_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LlmUpstreamError as exc:
                if exc.status_code in (403, 404) and attempt_model != self._default_model:
                    logger.warning(
                        "LLM model %s rejected (%s); retrying with %s",
                        attempt_model,
                        exc.status_code,
                        self._default_model,
                    )
                    last_error = exc
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise LlmUpstreamError("LLM request failed with no model candidates")
