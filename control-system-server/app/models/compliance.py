from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

DSR_TYPES = ("access", "export", "delete")
DSR_STATUSES = ("received", "in_progress", "completed", "rejected", "blocked_legal_hold")


class DsrRequest(Base):
    """NDPR-oriented data subject request workflow (access / export / delete)."""

    __tablename__ = "dsr_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    request_type: Mapped[str] = mapped_column(String(16), index=True)  # access | export | delete
    subject_email: Mapped[str] = mapped_column(String(255), index=True)
    subject_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    evidence_ref: Mapped[str] = mapped_column(String(255), default="")  # ticket / ticket URL
    export_uri: Mapped[str] = mapped_column(String(512), default="")  # stub path / signed URL placeholder
    assigned_to: Mapped[str] = mapped_column(String(255), default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Subprocessor(Base):
    """Current subprocessor register (Paystack, AT, AI vendors, …)."""

    __tablename__ = "subprocessors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(255), default="")
    region: Mapped[str] = mapped_column(String(64), default="global")
    dpa_url: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
