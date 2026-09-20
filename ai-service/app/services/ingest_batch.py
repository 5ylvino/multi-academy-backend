from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.copilot import IngestBatchItem, IngestBatchRequest, IngestBatchResponse
from app.schemas.ingest import IngestDocumentRequest
from app.services.ingestion import IngestionService


class IngestBatchService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._ingestion = IngestionService(db)

    async def ingest_items(self, tenant_id: str, payload: IngestBatchRequest) -> IngestBatchResponse:
        document_ids: list[str] = []
        failed = 0
        for item in payload.items:
            try:
                result = await self._ingestion.ingest_document(
                    tenant_id,
                    IngestDocumentRequest(
                        sourceType=item.source_type,
                        sourceId=item.source_id,
                        title=item.title,
                        text=item.text,
                        visibility=item.visibility,
                        subjectId=item.subject_id,
                        classId=item.class_id,
                    ),
                )
                document_ids.append(result.document_id)
            except Exception:
                failed += 1
        return IngestBatchResponse(
            ingested=len(document_ids),
            failed=failed,
            documentIds=document_ids,
        )

    async def ingest_scheme_topics(
        self,
        tenant_id: str,
        items: list[IngestBatchItem],
    ) -> IngestBatchResponse:
        normalized = [
            IngestBatchItem(
                sourceType=item.source_type or "scheme_topic",
                sourceId=item.source_id,
                title=item.title,
                text=item.text,
                visibility=item.visibility or "staff",
                subjectId=item.subject_id,
                classId=item.class_id,
            )
            for item in items
        ]
        return await self.ingest_items(tenant_id, IngestBatchRequest(items=normalized))

    async def ingest_lesson_notes(
        self,
        tenant_id: str,
        items: list[IngestBatchItem],
    ) -> IngestBatchResponse:
        normalized = [
            IngestBatchItem(
                sourceType=item.source_type or "lesson_note",
                sourceId=item.source_id,
                title=item.title,
                text=item.text,
                visibility=item.visibility or "staff",
                subjectId=item.subject_id,
                classId=item.class_id,
            )
            for item in items
        ]
        return await self.ingest_items(tenant_id, IngestBatchRequest(items=normalized))
