from __future__ import annotations

from app.chains.base import format_school_context, format_sources_block, load_prompt, run_llm_chat, to_ai_response
from app.providers.llm.base import LlmMessage
from app.schemas.chat import ChatRequest
from app.schemas.common import AiResponse
from app.security.context import RequestContext
from app.services.retrieval import RetrievalService
from app.utils.messages import safe_messages


class AssistantChain:
    def __init__(self, retrieval: RetrievalService | None = None) -> None:
        self._retrieval = retrieval or RetrievalService()

    async def run(self, ctx: RequestContext, payload: ChatRequest) -> AiResponse:
        messages = safe_messages(payload.messages)
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        sources = await self._retrieval.retrieve(tenant_id=ctx.tenant_id, query=last_user)
        system_prompt = (
            f"{load_prompt('assistant')}\n\n"
            f"School context (read-only):\n{format_school_context(payload.school_context)}\n\n"
            f"Retrieved documents:\n{format_sources_block(sources)}"
        )
        result = await run_llm_chat(
            ctx=ctx,
            system_prompt=system_prompt,
            messages=[LlmMessage(role=m.role, content=m.content) for m in messages],
            provider=payload.provider,
        )
        return to_ai_response(result, sources)
