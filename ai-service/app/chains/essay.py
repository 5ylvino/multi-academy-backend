from __future__ import annotations

import json
import re

from app.chains.base import run_llm_chat, to_ai_response
from app.providers.llm.base import LlmMessage
from app.schemas.chat import EssayGradeRequest, EssayGradeResponse
from app.security.context import RequestContext


def _parse_grade(content: str) -> tuple[float | None, str]:
    trimmed = content.strip()
    match = re.search(r"\{[\s\S]*\}", trimmed)
    if match:
        try:
            parsed = json.loads(match.group(0))
            score = float(parsed.get("score"))
            feedback = str(parsed.get("feedback") or "").strip()
            if feedback:
                return score, feedback
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return None, trimmed


class EssayChain:
    async def run(self, ctx: RequestContext, payload: EssayGradeRequest) -> EssayGradeResponse:
        rubric = payload.rubric or (
            'Score 0-100 on clarity, structure, grammar, and relevance. '
            'Return JSON only: {"score":number,"feedback":string}'
        )
        system_prompt = (
            f"You are an essay grading assistant. Rubric: {rubric}. "
            f"Max score: {payload.max_score}. Return JSON only."
        )
        result = await run_llm_chat(
            ctx=ctx,
            system_prompt=system_prompt,
            messages=[LlmMessage(role="user", content=payload.essay_text[:20000])],
            provider=payload.provider,
        )
        base = to_ai_response(result, [])
        score, feedback = _parse_grade(result.content)
        return EssayGradeResponse(
            content=result.content,
            providerId=base.provider_id,
            model=base.model,
            usage=base.usage,
            sources=[],
            score=score,
            feedback=feedback or base.content,
        )
