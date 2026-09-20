from __future__ import annotations

from app.chains.base import load_prompt, run_llm_chat
from app.jobs.mastery_rollup import MasteryRollupJob
from app.providers.llm.base import LlmMessage
from app.schemas.insights import (
    AtRiskInsightsRequest,
    AtRiskInsightsResponse,
    ClassInsightsRequest,
    ClassInsightsResponse,
    InsightFlag,
    MasteryRollupRequest,
    MasteryRollupResponse,
)
from app.security.context import RequestContext
from app.services.at_risk_ml import AtRiskMlScorer


class InsightsService:
    def __init__(
        self,
        rollup_job: MasteryRollupJob | None = None,
        ml_scorer: AtRiskMlScorer | None = None,
    ) -> None:
        self._rollup = rollup_job or MasteryRollupJob()
        self._ml = ml_scorer or AtRiskMlScorer()

    async def detect_class(
        self,
        ctx: RequestContext,
        payload: ClassInsightsRequest,
    ) -> ClassInsightsResponse:
        rollup = self._rollup.run(
            tenant_id=ctx.tenant_id,
            payload=MasteryRollupRequest(
                classId=payload.class_id,
                subjectId=payload.subject_id,
                masteryThreshold=payload.mastery_threshold,
                studentMastery=payload.student_mastery,
            ),
        )
        flags: list[InsightFlag] = []
        for cell in rollup.rollups:
            if cell.below_threshold_count <= 0:
                continue
            topic_label = cell.topic_key.split(":", 1)[-1]
            flags.append(
                InsightFlag(
                    topic=topic_label,
                    subjectId=cell.subject_id,
                    message=(
                        f"{cell.below_threshold_count} students below mastery — {topic_label}"
                    ),
                    studentCount=cell.student_count,
                    belowThresholdCount=cell.below_threshold_count,
                    severity=cell.severity,
                )
            )

        narration: str | None = None
        if flags:
            try:
                flag_lines = "\n".join(f"- {f.message}" for f in flags[:6])
                result = await run_llm_chat(
                    ctx=ctx,
                    system_prompt=(
                        "Summarize class learning gaps for a teacher in 2-3 sentences. "
                        "Be specific and actionable. Do not name individual students."
                    ),
                    messages=[LlmMessage(role="user", content=flag_lines)],
                    provider=payload.provider,
                )
                narration = result.content.strip()
            except Exception:
                narration = None
        if narration is None and flags:
            top = flags[0]
            narration = (
                f"Focus on {top.topic}: {top.below_threshold_count} of "
                f"{top.student_count} students are below the mastery threshold."
            )

        return ClassInsightsResponse(
            classId=payload.class_id,
            heatmap=rollup.rollups,
            flags=flags,
            narration=narration,
            disclaimer="Advisory signals only — verify with class assessments before interventions.",
        )

    async def at_risk(
        self,
        ctx: RequestContext,
        payload: AtRiskInsightsRequest,
    ) -> AtRiskInsightsResponse:
        students = []
        for row in payload.students:
            base = {
                "studentId": row.student_id,
                "currentAvg": row.current_avg,
                "previousAvg": row.previous_avg,
                "attendanceRate": row.attendance_rate,
                "riskLevel": row.risk_level,
                "weakTopics": row.weak_topics,
                "summary": self._student_summary(row),
            }
            students.append(self._ml.enrich_student(base))

        summary: str | None = None
        if students:
            high = sum(
                1
                for s in students
                if s.get("mlRiskLevel") == "high" or s.get("riskLevel") == "high"
            )
            summary = (
                f"{len(students)} students flagged; {high} high risk. "
                "Review weak topics and schedule recovery sessions."
            )
        return AtRiskInsightsResponse(
            count=len(students),
            students=students,
            summary=summary,
            disclaimer="Predictive risk is advisory — teacher confirmation required.",
        )

    def run_rollup(
        self,
        ctx: RequestContext,
        payload: MasteryRollupRequest,
    ) -> MasteryRollupResponse:
        return self._rollup.run(tenant_id=ctx.tenant_id, payload=payload)

    def _student_summary(self, row) -> str:
        parts = []
        if row.current_avg is not None and row.previous_avg is not None:
            delta = row.current_avg - row.previous_avg
            if delta < -5:
                parts.append("grades falling")
        if row.attendance_rate is not None and row.attendance_rate < 80:
            parts.append("attendance concern")
        if row.weak_topics:
            parts.append(f"weak in {row.weak_topics[0]}")
        return ", ".join(parts) if parts else "monitor closely"
