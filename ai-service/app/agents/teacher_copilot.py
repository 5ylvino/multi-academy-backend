from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.chains.base import load_prompt, run_llm_chat
from app.db.models import AiInterventionPlan
from app.db.session import SessionLocal
from app.providers.llm.base import LlmMessage
from app.schemas.copilot import (
    CopilotProposal,
    InterventionApproveRequest,
    InterventionListItem,
    InterventionPlanBody,
    InterventionPlanRequest,
    InterventionPlanResponse,
)
from app.security.context import RequestContext


def _parse_plan(content: str) -> dict | None:
    match = re.search(r"\{[\s\S]*\}", content.strip())
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


class TeacherCopilotAgent:
    async def create_plan(
        self,
        ctx: RequestContext,
        payload: InterventionPlanRequest,
        *,
        db: Session | None = None,
    ) -> InterventionPlanResponse:
        session = db or SessionLocal()
        owns = db is None
        context_lines = [
            f"Class: {payload.class_name or payload.class_id}",
            f"Subject: {payload.subject_name or payload.subject_id or 'General'}",
            f"Weak topic: {payload.topic}",
        ]
        if payload.weak_topic:
            wt = payload.weak_topic
            context_lines.append(
                f"Mastery data: {wt.below_threshold_count}/{wt.student_count} below threshold, "
                f"avg {wt.avg_mastery_pct}%"
            )
        result = await run_llm_chat(
            ctx=ctx,
            system_prompt=load_prompt("teacher_copilot"),
            messages=[LlmMessage(role="user", content="\n".join(context_lines))],
            provider=payload.provider,
        )
        parsed = _parse_plan(result.content) or {}
        proposals = [
            CopilotProposal.model_validate(item)
            for item in parsed.get("proposals", [])
            if isinstance(item, dict)
        ]
        if not proposals:
            proposals = [
                CopilotProposal(
                    type="reteach",
                    title=f"Re-teach {payload.topic}",
                    detail=parsed.get("reteachSuggestion"),
                    requiresApproval=True,
                ),
                CopilotProposal(
                    type="assignment",
                    title=f"Assign {parsed.get('questionCount', 5)} practice questions",
                    requiresApproval=True,
                ),
            ]
        plan_body = InterventionPlanBody(
            reteachSuggestion=str(parsed.get("reteachSuggestion") or f"Re-teach {payload.topic} with worked examples."),
            questionCount=int(parsed.get("questionCount") or 5),
            smallGroupRecommendation=str(
                parsed.get("smallGroupRecommendation")
                or "Pull 4–6 students who scored lowest for a 15-minute recap."
            ),
            parentCoachingNote=str(
                parsed.get("parentCoachingNote")
                or "Share a 15-minute home practice checklist for this topic."
            ),
            proposals=proposals[:8],
        )
        plan_id = uuid.uuid4()
        row = AiInterventionPlan(
            id=plan_id,
            tenant_id=ctx.tenant_id,
            teacher_id=ctx.actor_id,
            class_id=payload.class_id,
            subject_id=payload.subject_id,
            topic=payload.topic,
            status="draft",
            plan_json=plan_body.model_dump(by_alias=True),
        )
        try:
            session.add(row)
            session.commit()
            return InterventionPlanResponse(
                planId=str(plan_id),
                status=row.status,
                classId=payload.class_id,
                subjectId=payload.subject_id,
                topic=payload.topic,
                plan=plan_body,
                disclaimer="Draft plan — teacher must approve before assignments or parent messages send.",
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def get_plan(
        self,
        ctx: RequestContext,
        plan_id: str,
        *,
        db: Session | None = None,
    ) -> InterventionPlanResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            row = (
                session.query(AiInterventionPlan)
                .filter(
                    AiInterventionPlan.id == uuid.UUID(plan_id),
                    AiInterventionPlan.tenant_id == ctx.tenant_id,
                )
                .one_or_none()
            )
            if row is None:
                raise ValueError("Intervention plan not found")
            if row.teacher_id != ctx.actor_id and "principal" not in ctx.roles and "director" not in ctx.roles:
                raise PermissionError("Not allowed to view this plan")
            return InterventionPlanResponse(
                planId=str(row.id),
                status=row.status,
                classId=row.class_id,
                subjectId=row.subject_id,
                topic=row.topic,
                plan=InterventionPlanBody.model_validate(row.plan_json),
                disclaimer="Draft plan — teacher must approve before assignments or parent messages send.",
            )
        finally:
            if owns:
                session.close()

    def list_plans(
        self,
        ctx: RequestContext,
        *,
        status: str | None = None,
        db: Session | None = None,
    ) -> list[InterventionListItem]:
        session = db or SessionLocal()
        owns = db is None
        try:
            query = session.query(AiInterventionPlan).filter(
                AiInterventionPlan.tenant_id == ctx.tenant_id,
                AiInterventionPlan.teacher_id == ctx.actor_id,
            )
            if status:
                query = query.filter(AiInterventionPlan.status == status)
            rows = query.order_by(AiInterventionPlan.created_at.desc()).limit(50).all()
            return [
                InterventionListItem(
                    planId=str(row.id),
                    status=row.status,
                    classId=row.class_id,
                    topic=row.topic,
                    createdAt=row.created_at.isoformat() if row.created_at else None,
                )
                for row in rows
            ]
        finally:
            if owns:
                session.close()

    def review_plan(
        self,
        ctx: RequestContext,
        plan_id: str,
        payload: InterventionApproveRequest,
        *,
        db: Session | None = None,
    ) -> InterventionPlanResponse:
        session = db or SessionLocal()
        owns = db is None
        action = payload.action.strip().lower()
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        try:
            row = (
                session.query(AiInterventionPlan)
                .filter(
                    AiInterventionPlan.id == uuid.UUID(plan_id),
                    AiInterventionPlan.tenant_id == ctx.tenant_id,
                )
                .one_or_none()
            )
            if row is None:
                raise ValueError("Intervention plan not found")
            if row.teacher_id != ctx.actor_id and "principal" not in ctx.roles and "director" not in ctx.roles:
                raise PermissionError("Not allowed to review this plan")
            if row.status != "draft":
                raise ValueError("Plan is not in draft status")
            row.status = "approved" if action == "approve" else "rejected"
            if action == "approve":
                row.approved_by = ctx.actor_id
                row.approved_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return InterventionPlanResponse(
                planId=str(row.id),
                status=row.status,
                classId=row.class_id,
                subjectId=row.subject_id,
                topic=row.topic,
                plan=InterventionPlanBody.model_validate(row.plan_json),
                disclaimer=(
                    "Approved plan — execute proposals manually until automated workflows are enabled."
                    if row.status == "approved"
                    else "Plan rejected."
                ),
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()
