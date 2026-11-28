"""Audit trail responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.presentation.http.v1.schemas import ApiModel, PageMeta


class AuditEventResponse(ApiModel):
    id: UUID
    at: datetime = Field(description="When it happened (UTC).")
    action: str = Field(description="Stable code, e.g. finance.payment_recorded.")
    label: str = Field(description="What happened, in plain English.")
    category: str = Field(description="security, people, academics, attendance, finance, ...")
    school_id: UUID | None = Field(description="Empty for owner-console activity.")
    actor_id: UUID | None = Field(description="Who did it. Empty for automatic work.")
    actor_type: str | None = Field(description="school or platform.")
    actor_name: str | None
    actor_email: str | None
    target_type: str = Field(description="What it was done to, e.g. invoice, student, class.")
    target_id: str
    details: dict[str, Any] = Field(description="What changed. Updates list each field's from/to.")
    request_id: str | None = Field(description="Matches the X-Request-Id of the request.")
    ip_address: str | None
    device: str | None = Field(description="Browser or app that sent the request.")


class AuditPageResponse(ApiModel):
    items: list[AuditEventResponse]
    page: PageMeta


class AuditActionResponse(ApiModel):
    action: str
    label: str
    category: str
