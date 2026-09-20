from fastapi import APIRouter, Depends, Query

from app.jobs.conversation_retention import ConversationRetentionJob
from app.security.context import RequestContext
from app.security.features import require_feature

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.post("/conversation-retention")
async def conversation_retention(
    ctx: RequestContext = Depends(require_feature("ai.assistant")),
    retention_days: int = Query(default=90, alias="retentionDays"),
) -> dict:
    job = ConversationRetentionJob(retention_days=retention_days)
    result = job.run(tenant_id=ctx.tenant_id)
    return result
