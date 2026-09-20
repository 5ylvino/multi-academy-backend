from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

TICKET_STATUSES = ("open", "in_progress", "waiting", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_REQUEST_TYPES = ("contact", "demo")


class CustomerTicket(Base):
    __tablename__ = "customer_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    request_type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str] = mapped_column(String(64), default="")
    organization: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    assigned_to: Mapped[str] = mapped_column(String(255), default="")
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    notes: Mapped[list["TicketNote"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketNote.created_at"
    )


class TicketNote(Base):
    __tablename__ = "ticket_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("customer_tickets.id", ondelete="CASCADE"), index=True)
    author_email: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    ticket: Mapped[CustomerTicket] = relationship(back_populates="notes")
