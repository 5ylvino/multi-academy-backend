from fastapi import APIRouter, Depends

from app.chains.timetable_explain import TimetableExplainChain
from app.schemas.timetable import TimetableSolveRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event
from app.services.timetable_solver import solve_with_ortools

router = APIRouter(prefix="/v1/timetable", tags=["timetable"])


@router.post("/solve")
async def solve_timetable(
    payload: TimetableSolveRequest,
    ctx: RequestContext = Depends(require_feature("ai.timetable_solver")),
) -> dict:
    with audit_timer() as timing:
        try:
            result = solve_with_ortools(payload)
            if payload.explain and result.suggestions:
                result = await TimetableExplainChain().enrich(ctx, result)
            write_audit_event(
                ctx=ctx,
                feature="ai.timetable_solver",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                latency_ms=timing["latency_ms"],
            )
            return result.model_dump(by_alias=True)
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.timetable_solver",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
