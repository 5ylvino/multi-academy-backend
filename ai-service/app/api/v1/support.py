from fastapi import APIRouter, Depends

from app.chains.support import SupportChain
from app.schemas.chat import ChatRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/support", tags=["support"])


@router.post("/chat")
async def support_chat(
    payload: ChatRequest,
    ctx: RequestContext = Depends(require_feature("ai.support_chatbot")),
) -> dict:
    chain = SupportChain()
    with audit_timer() as timing:
        try:
            response = await chain.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.support_chatbot",
                provider_id=response.provider_id,
                model=response.model,
                usage=response.usage,
                sources=response.sources,
                latency_ms=timing["latency_ms"],
            )
            return response.model_dump(by_alias=True)
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.support_chatbot",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
