from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

COMPONENT_STATUSES = ("operational", "degraded", "outage", "maintenance")
INCIDENT_STATUSES = ("investigating", "identified", "monitoring", "resolved")


class StatusComponent(Base):
    """Public status-page components (API, payments, SMS, …)."""

    __tablename__ = "status_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="operational")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="investigating", index=True)
    impact: Mapped[str] = mapped_column(String(32), default="minor")  # minor|major|critical
    summary: Mapped[str] = mapped_column(Text, default="")
    affected_components: Mapped[list] = mapped_column(JSON, default=list)
    updates: Mapped[list] = mapped_column(JSON, default=list)  # [{at, status, message, by}]
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    postmortem_url: Mapped[str] = mapped_column(String(512), default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
