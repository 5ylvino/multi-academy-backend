from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.db.models import AiDocument, AiDocumentChunk
from app.deps import get_pinecone_client
from app.providers.embeddings.nvidia_kilo import NvidiaKiloEmbeddings
from app.schemas.ingest import IngestDocumentRequest, IngestDocumentResponse


def _chunk_text(text: str, chunk_size: int = 700, overlap: int = 80) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= chunk_size:
        return [normalized] if normalized else []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


class IngestionService:
    def __init__(self, db: Session, embedder: NvidiaKiloEmbeddings | None = None) -> None:
        self._db = db
        self._embedder = embedder or NvidiaKiloEmbeddings()
        self._pinecone = get_pinecone_client()

    async def ingest_document(self, tenant_id: str, payload: IngestDocumentRequest) -> IngestDocumentResponse:
        checksum = hashlib.sha256(payload.text.encode("utf-8")).hexdigest()
        document = AiDocument(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            title=payload.title,
            visibility=payload.visibility,
            status="processing",
            checksum=checksum,
        )
        self._db.add(document)
        self._db.flush()

        chunks = _chunk_text(payload.text)
        vectors: list[dict] = []
        chunk_rows: list[AiDocumentChunk] = []

        embeddings: list[list[float]] = []
        if chunks and self._embedder.configured and self._pinecone.configured:
            try:
                embeddings = await self._embedder.embed(chunks)
            except Exception:
                embeddings = []

        for index, chunk in enumerate(chunks):
            chunk_id = uuid.uuid4()
            pinecone_id = f"{document.id}:{index}"
            row = AiDocumentChunk(
                id=chunk_id,
                document_id=document.id,
                tenant_id=tenant_id,
                chunk_index=index,
                pinecone_id=pinecone_id if embeddings else None,
                token_count=len(chunk.split()),
                text_preview=chunk[:300],
            )
            chunk_rows.append(row)
            if embeddings and index < len(embeddings):
                vectors.append(
                    {
                        "id": pinecone_id,
                        "values": embeddings[index],
                        "metadata": {
                            "tenant_id": tenant_id,
                            "source_type": payload.source_type,
                            "source_id": payload.source_id,
                            "document_id": str(document.id),
                            "chunk_index": index,
                            "visibility": payload.visibility,
                            "title": payload.title or "",
                            "text_preview": chunk[:300],
                        },
                    }
                )

        self._db.add_all(chunk_rows)
        if vectors:
            self._pinecone.upsert(tenant_id=tenant_id, vectors=vectors)

        document.status = "ready" if chunks else "empty"
        self._db.commit()

        return IngestDocumentResponse(
            documentId=str(document.id),
            chunkCount=len(chunks),
            status=document.status,
        )
