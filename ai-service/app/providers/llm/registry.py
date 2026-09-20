from app.config import Settings, get_settings
from app.providers.llm.base import LlmProvider
from app.providers.llm.nvidia_kilo import NvidiaKiloProvider


class LlmRegistry:
    """Resolve LLM provider by id; default to NVIDIA/Kilo free models."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._providers: dict[str, LlmProvider] = {
            "nvidia": NvidiaKiloProvider(self._settings, mode="nvidia"),
            "kilo": NvidiaKiloProvider(self._settings, mode="kilo"),
        }

    def register(self, provider: LlmProvider) -> None:
        self._providers[provider.id] = provider

    def resolve(self, provider_id: str | None = None) -> LlmProvider:
        pid = (provider_id or self._settings.llm_provider).strip().lower()
        provider = self._providers.get(pid)
        if provider is None:
            raise ValueError(f"Unknown LLM provider: {pid}")
        return provider
