from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LlmMessage:
    role: str
    content: str


@dataclass
class LlmUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class LlmResponse:
    content: str
    model: str
    provider_id: str
    usage: LlmUsage | None = None
    raw: dict[str, Any] | None = None


class LlmProvider(ABC):
    """Swappable LLM adapter — mirrors NestJS AiProvider contract."""

    id: str

    @abstractmethod
    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tenant_id: str | None = None,
    ) -> LlmResponse:
        raise NotImplementedError

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise NotImplementedError(f"{self.id} does not support embeddings")
