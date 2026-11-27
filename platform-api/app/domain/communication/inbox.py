"""The notification inbox: short, personal notices ("New message from Mrs Ade")."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.shared.ids import new_id


class NotificationKind(StrEnum):
    MESSAGE = "message"  # one unread notice per conversation, refreshed by each new message
    PAYSLIP = "payslip"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class Notification:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    kind: NotificationKind
    title: str
    body: str
    link_type: str | None
    link_id: UUID | None
    created_at: datetime
    read_at: datetime | None = None

    @classmethod
    def new(
        cls,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: NotificationKind,
        title: str,
        body: str,
        link_type: str | None,
        link_id: UUID | None,
        now: datetime,
    ) -> Notification:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind,
            title=" ".join(title.split())[:160],
            body=" ".join(body.split())[:280],
            link_type=link_type,
            link_id=link_id,
            created_at=now,
        )
