from __future__ import annotations

from collections import defaultdict

from app.chains.base import load_prompt, run_llm_chat
from app.providers.llm.base import LlmMessage
from app.schemas.guidance import StudentSignal
from app.schemas.readiness import JambReadinessRequest, JambReadinessResponse, SubjectReadiness
from app.security.context import RequestContext
from app.services.mastery import MasteryService


class JambReadinessChain:
    def __init__(self, mastery: MasteryService | None = None) -> None:
        self._mastery = mastery or MasteryService()

    async def run(self, ctx: RequestContext, payload: JambReadinessRequest) -> JambReadinessResponse:
        signals = payload.student_signals
        if signals:
            self._mastery.rollup_from_signals(tenant_id=ctx.tenant_id, signals=signals)
        subject_scores: dict[str, list[int]] = defaultdict(list)
        subject_names: dict[str, str] = {}
        attempt_counts: dict[str, int] = defaultdict(int)

        if signals:
            for sig in signals.signals:
                if sig.source != "cbt" or sig.score is None or not sig.max_score:
                    continue
                key = sig.subject or sig.topic or "General"
                subject_names[key] = key
                pct = int(round((sig.score / sig.max_score) * 100))
                subject_scores[key].append(pct)
                attempt_counts[key] += 1

        mastery_items = self._mastery.list_mastery(
            tenant_id=ctx.tenant_id,
            student_id=payload.student_id,
        )
        for item in mastery_items:
            label = item.topic_key.split(":", 1)[-1]
            subject_scores[label].append(item.mastery_pct)
            subject_names[label] = label

        subjects: list[SubjectReadiness] = []
        for name, scores in subject_scores.items():
            if not scores:
                continue
            avg = int(round(sum(scores) / len(scores)))
            trend = "up" if len(scores) > 1 and scores[0] < scores[-1] else "stable"
            subjects.append(
                SubjectReadiness(
                    subjectName=name,
                    readinessPct=avg,
                    attemptCount=attempt_counts.get(name, len(scores)),
                    examType="jamb",
                    trend=trend,
                )
            )
        subjects.sort(key=lambda s: s.readiness_pct)

        summary: str | None = None
        if subjects:
            weakest = subjects[0]
            summary = (
                f"Focus revision on {weakest.subject_name} ({weakest.readiness_pct}% readiness). "
                f"Timed CBT practice improves exam-day confidence."
            )
            if payload.provider:
                try:
                    lines = ", ".join(f"{s.subject_name} {s.readiness_pct}%" for s in subjects[:5])
                    result = await run_llm_chat(
                        ctx=ctx,
                        system_prompt=load_prompt("study_plan"),
                        messages=[
                            LlmMessage(
                                role="user",
                                content=f"Summarize JAMB readiness in 2 sentences: {lines}",
                            )
                        ],
                        provider=payload.provider,
                    )
                    summary = result.content.strip()[:500]
                except Exception:
                    pass

        if not subjects:
            subjects = [
                SubjectReadiness(subjectName="Mathematics", readinessPct=0, attemptCount=0, trend="stable"),
                SubjectReadiness(subjectName="English", readinessPct=0, attemptCount=0, trend="stable"),
            ]
            summary = "Complete timed CBT mocks to generate live readiness scores."

        return JambReadinessResponse(
            studentId=payload.student_id,
            subjects=subjects[:8],
            summary=summary,
            disclaimer="Readiness scores are advisory from CBT practice — not official JAMB/WAEC predictions.",
        )
