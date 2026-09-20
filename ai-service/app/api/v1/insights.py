from fastapi import APIRouter, Depends

from app.schemas.insights import AtRiskInsightsRequest, ClassInsightsRequest, MasteryRollupRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event
from app.services.insights import InsightsService

router = APIRouter(prefix="/v1/insights", tags=["insights"])


@router.post("/detect")
async def insights_detect(
    payload: ClassInsightsRequest,
    ctx: RequestContext = Depends(require_feature("ai.performance_detection")),
) -> dict:
    service = InsightsService()
    with audit_timer() as timing:
        try:
            response = await service.detect_class(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.performance_detection",
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
                feature="ai.performance_detection",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.post("/at-risk")
async def insights_at_risk(
    payload: AtRiskInsightsRequest,
    ctx: RequestContext = Depends(require_feature("ai.risk_analytics")),
) -> dict:
    service = InsightsService()
    with audit_timer() as timing:
        try:
            response = await service.at_risk(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.risk_analytics",
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
                feature="ai.risk_analytics",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.post("/mastery-rollup")
async def mastery_rollup(
    payload: MasteryRollupRequest,
    ctx: RequestContext = Depends(require_feature("ai.performance_detection")),
) -> dict:
    service = InsightsService()
    response = service.run_rollup(ctx, payload)
    return response.model_dump(by_alias=True)
