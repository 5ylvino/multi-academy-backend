from __future__ import annotations

import json

from app.chains.base import load_prompt, run_llm_chat
from app.providers.llm.base import LlmMessage
from app.schemas.timetable import SwapSuggestion, TimetableSolveResponse
from app.security.context import RequestContext


class TimetableExplainChain:
    async def enrich(
        self,
        ctx: RequestContext,
        result: TimetableSolveResponse,
    ) -> TimetableSolveResponse:
        if not result.suggestions:
            return result

        system = load_prompt("timetable_explain")
        payload = [
            {
                "slotId": s.slot_id,
                "clashCount": s.clash_count,
                "current": f"day {s.current_day} {s.current_start}-{s.current_end}",
                "proposed": (
                    f"day {s.proposed_day} {s.proposed_start}-{s.proposed_end}"
                    if s.proposed_day is not None
                    else None
                ),
                "solverNote": s.suggestion,
            }
            for s in result.suggestions
        ]
        user_content = (
            f"Class {result.class_id} has {result.clash_count} clashing slot(s).\n"
            f"Explain each suggestion for a school admin in plain language.\n"
            f"Return JSON array: [{{\"slotId\":\"...\",\"explanation\":\"...\"}}]\n\n"
            f"{json.dumps(payload, indent=2)}"
        )
        try:
            llm_result = await run_llm_chat(
                ctx=ctx,
                system_prompt=system,
                messages=[LlmMessage(role="user", content=user_content)],
                provider=None,
            )
            text = llm_result.content.strip()
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                rows = json.loads(text[start : end + 1])
                by_slot = {
                    str(row.get("slotId")): str(row.get("explanation") or "").strip()
                    for row in rows
                    if isinstance(row, dict)
                }
                enriched: list[SwapSuggestion] = []
                for suggestion in result.suggestions:
                    explanation = by_slot.get(suggestion.slot_id)
                    enriched.append(
                        suggestion.model_copy(update={"explanation": explanation or suggestion.suggestion})
                    )
                return result.model_copy(update={"suggestions": enriched})
        except Exception:
            pass
        return result
