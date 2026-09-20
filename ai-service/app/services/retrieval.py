from __future__ import annotations

from app.deps import get_pinecone_client
from app.providers.embeddings.nvidia_kilo import NvidiaKiloEmbeddings
from app.schemas.common import SourceCitation


class RetrievalService:
    def __init__(self, embedder: NvidiaKiloEmbeddings | None = None) -> None:
        self._embedder = embedder or NvidiaKiloEmbeddings()
        self._pinecone = get_pinecone_client()

    async def retrieve(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int = 8,
        visibility: str | None = None,
    ) -> list[SourceCitation]:
        if not query.strip() or not self._embedder.configured or not self._pinecone.configured:
            return []
        try:
            vectors = await self._embedder.embed([query])
            if not vectors:
                return []
            metadata_filter = {"visibility": visibility} if visibility else None
            matches = self._pinecone.query(
                tenant_id=tenant_id,
                vector=vectors[0],
                top_k=top_k,
                metadata_filter=metadata_filter,
            )
        except Exception:
            return []

        citations: list[SourceCitation] = []
        for match in matches:
            meta = match.get("metadata") or {}
            citations.append(
                SourceCitation(
                    type=str(meta.get("source_type") or "document"),
                    id=str(meta.get("source_id") or match.get("id") or ""),
                    chunkId=str(match.get("id") or ""),
                    excerpt=str(meta.get("text_preview") or "")[:300] or None,
                )
            )
        return citations
