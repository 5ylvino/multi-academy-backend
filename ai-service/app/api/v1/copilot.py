from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.teacher_copilot import TeacherCopilotAgent
from app.schemas.copilot import InterventionApproveRequest, InterventionPlanRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/copilot", tags=["copilot"])


@router.post("/intervention")
async def create_intervention(
    payload: InterventionPlanRequest,
    ctx: RequestContext = Depends(require_feature("ai.teacher_copilot")),
) -> dict:
    agent = TeacherCopilotAgent()
    with audit_timer() as timing:
        try:
            response = await agent.create_plan(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.teacher_copilot",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                latency_ms=timing["latency_ms"],
            )
            return response.model_dump(by_alias=True)
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.teacher_copilot",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.get("/intervention/{plan_id}")
async def get_intervention(
    plan_id: str,
    ctx: RequestContext = Depends(require_feature("ai.teacher_copilot")),
) -> dict:
    agent = TeacherCopilotAgent()
    try:
        response = agent.get_plan(ctx, plan_id)
        return response.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/interventions")
async def list_interventions(
    status: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_feature("ai.teacher_copilot")),
) -> dict:
    agent = TeacherCopilotAgent()
    items = agent.list_plans(ctx, status=status)
    return {"items": [item.model_dump(by_alias=True) for item in items]}


@router.post("/intervention/{plan_id}/review")
async def review_intervention(
    plan_id: str,
    payload: InterventionApproveRequest,
    ctx: RequestContext = Depends(require_feature("ai.teacher_copilot")),
) -> dict:
    agent = TeacherCopilotAgent()
    try:
        response = agent.review_plan(ctx, plan_id, payload)
        write_audit_event(
            ctx=ctx,
            feature="ai.teacher_copilot",
            provider_id=None,
            model=None,
            usage=None,
            sources=[],
            status="ok",
        )
        return response.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
