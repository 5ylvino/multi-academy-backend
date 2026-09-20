from __future__ import annotations

import httpx

from app.config import Settings, get_settings

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaKiloEmbeddings:
    """OpenAI-compatible embeddings via NVIDIA NIM or Kilo gateway."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._model = self._settings.embedding_model

    def _resolve_endpoint(self) -> tuple[str, str]:
        kilo_key = (self._settings.kilo_api_key or "").strip()
        nvidia_key = (self._settings.nvidia_api_key or "").strip()

        if nvidia_key.startswith("nvapi-"):
            return NVIDIA_NIM_BASE_URL, nvidia_key
        if kilo_key:
            return self._settings.kilo_base_url.rstrip("/"), kilo_key
        if nvidia_key:
            return NVIDIA_NIM_BASE_URL, nvidia_key
        return self._settings.kilo_base_url.rstrip("/"), ""

    @property
    def configured(self) -> bool:
        _, api_key = self._resolve_endpoint()
        return bool(api_key)

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        base_url, api_key = self._resolve_endpoint()
        if not api_key:
            raise RuntimeError("Embedding API key is not configured")
        chosen_model = (model or self._model).strip()
        timeout = self._settings.request_timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(
                f"{base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": chosen_model, "input": texts},
            )
            res.raise_for_status()
            data = res.json()
        rows = sorted(data.get("data", []), key=lambda row: row.get("index", 0))
        return [row["embedding"] for row in rows]
