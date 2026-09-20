from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.copilot import IngestBatchRequest, IngestBatchResponse
from app.schemas.ingest import IngestDocumentRequest, IngestDocumentResponse
from app.services.ingest_batch import IngestBatchService
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/document", response_model=IngestDocumentResponse)
async def ingest_document(
    payload: IngestDocumentRequest,
    ctx: RequestContext = Depends(require_feature("ai.assistant")),
    db: Session = Depends(get_db),
) -> IngestDocumentResponse:
    service = IngestionService(db)
    return await service.ingest_document(ctx.tenant_id, payload)


@router.post("/scheme", response_model=IngestBatchResponse)
async def ingest_scheme(
    payload: IngestBatchRequest,
    ctx: RequestContext = Depends(require_feature("ai.assistant")),
    db: Session = Depends(get_db),
) -> IngestBatchResponse:
    service = IngestBatchService(db)
    return await service.ingest_scheme_topics(ctx.tenant_id, payload.items)


@router.post("/lesson-notes", response_model=IngestBatchResponse)
async def ingest_lesson_notes(
    payload: IngestBatchRequest,
    ctx: RequestContext = Depends(require_feature("ai.assistant")),
    db: Session = Depends(get_db),
) -> IngestBatchResponse:
    service = IngestBatchService(db)
    return await service.ingest_lesson_notes(ctx.tenant_id, payload.items)
