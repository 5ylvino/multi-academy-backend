from fastapi import APIRouter, Depends, Query

from app.chains.jamb_readiness import JambReadinessChain
from app.schemas.guidance import StudentSignals
from app.schemas.readiness import JambReadinessRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/readiness", tags=["readiness"])


@router.post("/jamb")
async def jamb_readiness_post(
    payload: JambReadinessRequest,
    ctx: RequestContext = Depends(require_feature("academic.jamb_cbt")),
) -> dict:
    chain = JambReadinessChain()
    with audit_timer() as timing:
        response = await chain.run(ctx, payload)
        write_audit_event(
            ctx=ctx,
            feature="academic.jamb_cbt",
            provider_id=None,
            model=None,
            usage=None,
            sources=[],
            latency_ms=timing["latency_ms"],
        )
        return response.model_dump(by_alias=True)


@router.get("/jamb")
async def jamb_readiness_get(
    student_id: str = Query(alias="studentId"),
    ctx: RequestContext = Depends(require_feature("academic.jamb_cbt")),
) -> dict:
    chain = JambReadinessChain()
    response = await chain.run(
        ctx,
        JambReadinessRequest(studentId=student_id, studentSignals=None),
    )
    return response.model_dump(by_alias=True)
