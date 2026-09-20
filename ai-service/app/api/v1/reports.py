from fastapi import APIRouter, Depends

from app.chains.report_comments import ReportCommentChain
from app.schemas.reports import ReportCommentRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.post("/comment-draft")
async def report_comment_draft(
    payload: ReportCommentRequest,
    ctx: RequestContext = Depends(require_feature("ai.report_comments")),
) -> dict:
    chain = ReportCommentChain()
    with audit_timer() as timing:
        try:
            response = await chain.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.report_comments",
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
                feature="ai.report_comments",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
