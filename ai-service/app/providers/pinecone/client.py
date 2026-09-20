from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings


@dataclass
class PineconeHealth:
    ok: bool
    message: str
    index_name: str | None = None


class PineconeClient:
    """Tenant-namespaced Pinecone wrapper."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None
        self._index: Any | None = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.pinecone_api_key.strip())

    def namespace_for(self, tenant_id: str) -> str:
        prefix = self._settings.pinecone_namespace_prefix.rstrip(":")
        return f"{prefix}:{tenant_id}"

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.configured:
            raise RuntimeError("PINECONE_API_KEY is not configured")
        from pinecone import Pinecone

        self._client = Pinecone(api_key=self._settings.pinecone_api_key)
        return self._client

    def index(self) -> Any:
        if self._index is not None:
            return self._index
        client = self._ensure_client()
        self._index = client.Index(self._settings.pinecone_index_name)
        return self._index

    def upsert(
        self,
        *,
        tenant_id: str,
        vectors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        namespace = self.namespace_for(tenant_id)
        for vector in vectors:
            metadata = dict(vector.get("metadata") or {})
            metadata["tenant_id"] = tenant_id
            vector["metadata"] = metadata
        result = self.index().upsert(vectors=vectors, namespace=namespace)
        return {"namespace": namespace, "upserted": getattr(result, "upserted_count", None)}

    def query(
        self,
        *,
        tenant_id: str,
        vector: list[float],
        top_k: int = 8,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        namespace = self.namespace_for(tenant_id)
        filt = {"tenant_id": {"$eq": tenant_id}}
        if metadata_filter:
            filt.update(metadata_filter)
        response = self.index().query(
            vector=vector,
            top_k=top_k,
            namespace=namespace,
            filter=filt,
            include_metadata=True,
        )
        matches = getattr(response, "matches", []) or []
        return [
            {
                "id": match.id,
                "score": match.score,
                "metadata": match.metadata or {},
            }
            for match in matches
        ]

    def health_check(self) -> PineconeHealth:
        if not self.configured:
            return PineconeHealth(ok=False, message="PINECONE_API_KEY missing")
        try:
            client = self._ensure_client()
            indexes = client.list_indexes()
            names = []
            if hasattr(indexes, "names"):
                names = list(indexes.names())
            elif isinstance(indexes, list):
                names = [getattr(i, "name", str(i)) for i in indexes]
            index_name = self._settings.pinecone_index_name
            if names and index_name not in names:
                return PineconeHealth(
                    ok=False,
                    message=f"Index {index_name!r} not found in Pinecone project",
                    index_name=index_name,
                )
            return PineconeHealth(ok=True, message="Pinecone reachable", index_name=index_name)
        except Exception as exc:
            return PineconeHealth(ok=False, message=str(exc), index_name=self._settings.pinecone_index_name)
