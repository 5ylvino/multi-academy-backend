import pytest

from app.config import Settings
from app.providers.llm.registry import LlmRegistry


def test_registry_resolves_nvidia_by_default() -> None:
    settings = Settings(LLM_PROVIDER="nvidia")
    registry = LlmRegistry(settings)
    provider = registry.resolve()
    assert provider.id == "nvidia"


def test_registry_unknown_provider_raises() -> None:
    registry = LlmRegistry()
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        registry.resolve("not-a-provider")
