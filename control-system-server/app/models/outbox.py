"""Config invalidation outbox (Phase B stub).

When configVersion bumps, an outbox row is written. A future Redis/pubsub or
webhook worker drains these; Nest currently polls by configVersion / TTL.
DR backup checklist lives on GET /v1/ops/dr-checklist.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # config.invalidate | tenant.status | billing.dunning | …
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), default="")
    aggregate_id: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Retry bookkeeping. Without these a permanently failing event was retried on
    # every batch forever and starved newer events out of the batch window.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    # pending | delivered | dead_letter
    delivery_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # JSON array of {at, ok, channel, message} delivery attempts
    delivery_log: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
