from fastapi import APIRouter, Depends

from app.chains.essay import EssayChain
from app.schemas.chat import EssayGradeRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/essay", tags=["essay"])


@router.post("/grade")
async def grade_essay(
    payload: EssayGradeRequest,
    ctx: RequestContext = Depends(require_feature("ai.essay_grading")),
) -> dict:
    chain = EssayChain()
    with audit_timer() as timing:
        try:
            response = await chain.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.essay_grading",
                provider_id=response.provider_id,
                model=response.model,
                usage=response.usage,
                sources=[],
                latency_ms=timing["latency_ms"],
            )
            return response.model_dump(by_alias=True)
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.essay_grading",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
