from fastapi import APIRouter, Depends

from app.agents.parent_coach import ParentCoachAgent
from app.chains.study_plan import StudyPlanChain
from app.schemas.guidance import ParentCoachingRequest, StudyPlanRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/guidance", tags=["guidance"])


@router.post("/home")
async def guidance_home(
    payload: ParentCoachingRequest,
    ctx: RequestContext = Depends(require_feature("ai.performance_recommendations")),
) -> dict:
    agent = ParentCoachAgent()
    with audit_timer() as timing:
        try:
            response = await agent.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.performance_recommendations",
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
                feature="ai.performance_recommendations",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.post("/study-plan")
async def guidance_study_plan(
    payload: StudyPlanRequest,
    ctx: RequestContext = Depends(require_feature("ai.performance_recommendations")),
) -> dict:
    chain = StudyPlanChain()
    with audit_timer() as timing:
        try:
            response = await chain.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.performance_recommendations",
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
                feature="ai.performance_recommendations",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
