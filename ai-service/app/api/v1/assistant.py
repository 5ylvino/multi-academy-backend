from fastapi import APIRouter, Depends

from app.chains.assistant import AssistantChain
from app.schemas.chat import ChatRequest
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


@router.post("/chat")
async def assistant_chat(
    payload: ChatRequest,
    ctx: RequestContext = Depends(require_feature("ai.assistant")),
) -> dict:
    chain = AssistantChain()
    with audit_timer() as timing:
        try:
            response = await chain.run(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.assistant",
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
                feature="ai.assistant",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise
