#!/usr/bin/env python3
"""Seed platform support FAQ into a tenant namespace for RAG."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import SessionLocal
from app.schemas.ingest import IngestDocumentRequest
from app.services.ingestion import IngestionService


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed MAS support FAQ into Pinecone/Neon")
    parser.add_argument("--tenant-id", required=True, help="Tenant id to ingest under")
    args = parser.parse_args()

    text = (Path(__file__).resolve().parents[1] / "seeds" / "faq_support.md").read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        service = IngestionService(db)
        result = await service.ingest_document(
            args.tenant_id,
            IngestDocumentRequest(
                sourceType="faq",
                sourceId="mas-support-v1",
                title="MAS Support FAQ",
                text=text,
                visibility="parent",
            ),
        )
        print(result.model_dump(by_alias=True))
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
