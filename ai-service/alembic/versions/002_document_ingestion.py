"""Document ingestion tables.

Revision ID: 002_documents
Revises: 001_initial
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_documents"
down_revision: Union[str, Sequence[str], None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("visibility", sa.String(length=32), nullable=False, server_default="staff"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_documents_tenant_id", "ai_documents", ["tenant_id"])
    op.create_index("ix_ai_documents_tenant_source", "ai_documents", ["tenant_id", "source_type", "source_id"])

    op.create_table(
        "ai_document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("pinecone_id", sa.String(length=128), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("text_preview", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_document_chunks_document_id", "ai_document_chunks", ["document_id"])
    op.create_index("ix_ai_document_chunks_tenant_id", "ai_document_chunks", ["tenant_id"])
    op.create_index("ix_ai_document_chunks_doc_idx", "ai_document_chunks", ["document_id", "chunk_index"])


def downgrade() -> None:
    op.drop_index("ix_ai_document_chunks_doc_idx", table_name="ai_document_chunks")
    op.drop_index("ix_ai_document_chunks_tenant_id", table_name="ai_document_chunks")
    op.drop_index("ix_ai_document_chunks_document_id", table_name="ai_document_chunks")
    op.drop_table("ai_document_chunks")

    op.drop_index("ix_ai_documents_tenant_source", table_name="ai_documents")
    op.drop_index("ix_ai_documents_tenant_id", table_name="ai_documents")
    op.drop_table("ai_documents")
