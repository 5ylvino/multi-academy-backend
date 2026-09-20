from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.deps import get_llm_registry
from app.providers.llm.base import LlmMessage, LlmResponse
from app.schemas.common import ProviderConfig, SourceCitation, UsageInfo
from app.schemas.common import AiResponse
from app.security.context import RequestContext


def load_prompt(name: str) -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / name / "v1.txt"
    return path.read_text(encoding="utf-8").strip()


def format_school_context(school_context: dict[str, Any] | str | None) -> str:
    if school_context is None:
        return "{}"
    if isinstance(school_context, str):
        return school_context[:12000]
    return json.dumps(school_context, default=str)[:12000]


def format_sources_block(sources: list[SourceCitation]) -> str:
    if not sources:
        return "No retrieved documents."
    lines = []
    for source in sources[:12]:
        excerpt = source.excerpt or ""
        lines.append(f"- [{source.type}:{source.id}] {excerpt}")
    return "\n".join(lines)


async def run_llm_chat(
    *,
    ctx: RequestContext,
    system_prompt: str,
    messages: list[LlmMessage],
    provider: ProviderConfig | None,
    settings: Settings | None = None,
) -> LlmResponse:
    cfg = settings or get_settings()
    registry = get_llm_registry()
    provider_id = (provider.provider_id if provider and provider.provider_id else cfg.llm_provider)
    llm = registry.resolve(provider_id)
    model = (provider.model if provider and provider.model else None) or cfg.llm_model
    max_tokens = (provider.max_tokens if provider and provider.max_tokens else None) or cfg.llm_max_tokens
    return await llm.chat(
        messages=[LlmMessage(role="system", content=system_prompt), *messages],
        model=model,
        max_tokens=max_tokens,
        temperature=cfg.llm_temperature,
        tenant_id=ctx.tenant_id,
    )


def to_ai_response(result: LlmResponse, sources: list[SourceCitation]) -> AiResponse:
    usage = None
    if result.usage:
        usage = UsageInfo(
            promptTokens=result.usage.prompt_tokens,
            completionTokens=result.usage.completion_tokens,
        )
    return AiResponse(
        content=result.content,
        providerId=result.provider_id,
        model=result.model,
        usage=usage,
        sources=sources,
    )
