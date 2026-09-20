from __future__ import annotations

import json
import re

from app.chains.base import load_prompt, run_llm_chat
from app.providers.llm.base import LlmMessage
from app.schemas.guidance import (
    CoachingResource,
    ParentCoachingRequest,
    ParentCoachingResponse,
)
from app.security.context import RequestContext
from app.services.mastery import MasteryService
from app.services.retrieval import RetrievalService


def _parse_coaching(content: str) -> dict | None:
    match = re.search(r"\{[\s\S]*\}", content.strip())
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


class ParentCoachAgent:
    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        mastery: MasteryService | None = None,
    ) -> None:
        self._retrieval = retrieval or RetrievalService()
        self._mastery = mastery or MasteryService()

    async def run(
        self,
        ctx: RequestContext,
        payload: ParentCoachingRequest,
    ) -> ParentCoachingResponse:
        self._mastery.rollup_from_signals(tenant_id=ctx.tenant_id, signals=payload.student_signals)
        signals = payload.student_signals
        focus_topic = "General study support"
        subject = None
        if signals and signals.weak_topics:
            focus_topic = signals.weak_topics[0]
        elif signals and signals.scheme_topics:
            focus_topic = signals.scheme_topics[0]
        query = payload.question or f"Parent coaching for {focus_topic}"
        sources = await self._retrieval.retrieve(tenant_id=ctx.tenant_id, query=query)
        retrieval_block = "\n".join(f"- {s.excerpt}" for s in sources[:8]) or "No documents."
        signal_block = "No student signals."
        if signals:
            signal_block = (
                f"Weak topics: {', '.join(signals.weak_topics) or 'none'}\n"
                f"Scheme topics: {', '.join(signals.scheme_topics) or 'none'}\n"
                f"Signals: {json.dumps([s.model_dump(by_alias=True) for s in signals.signals[:12]], default=str)}"
            )
        system = f"{load_prompt('parent_coach')}\n\nRetrieved:\n{retrieval_block}\n\nStudent data:\n{signal_block}"
        result = await run_llm_chat(
            ctx=ctx,
            system_prompt=system,
            messages=[LlmMessage(role="user", content=query)],
            provider=payload.provider,
        )
        parsed = _parse_coaching(result.content) or {}
        resources = [
            CoachingResource.model_validate(item)
            for item in parsed.get("resources", [])
            if isinstance(item, dict)
        ]
        tonight = [str(x) for x in parsed.get("tonightChecklist", []) if str(x).strip()]
        at_home = [str(x) for x in parsed.get("atHome", []) if str(x).strip()]
        if not at_home and tonight:
            at_home = tonight
        if not tonight and at_home:
            tonight = at_home[:3]
        topic = str(parsed.get("topic") or focus_topic)
        subject = parsed.get("subject") or subject
        return ParentCoachingResponse(
            studentId=payload.student_id,
            topic=topic,
            subject=subject,
            resources=resources[:6],
            tonightChecklist=tonight[:5],
            atHome=at_home[:6],
            sources=[f"{s.type}:{s.id}" for s in sources[:6]],
            disclaimer=(
                "This is study support for parents, not a diagnosis. "
                "It does not compare your child with classmates."
            ),
        )
