from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import CustomerTicket, TicketNote
from app.models.tickets import TICKET_PRIORITIES, TICKET_REQUEST_TYPES, TICKET_STATUSES
from app.services.customer_ticket_mailer import send_customer_email
from app.schemas import (
    PublicContactCreate,
    PublicContactOut,
    TicketNoteCreate,
    TicketNoteOut,
    TicketOut,
    TicketUpdate,
)

router = APIRouter(prefix="/v1/tickets", tags=["tickets"])
public_router = APIRouter(prefix="/v1/public", tags=["public-contact"])


@public_router.post("/contact", response_model=PublicContactOut, status_code=status.HTTP_201_CREATED)
def create_public_contact(body: PublicContactCreate, db: Session = Depends(get_db)):
    if body.website.strip():
        # Honeypot submissions receive the same shape as accepted submissions.
        return PublicContactOut(ticket_number="RECEIVED", message="Thanks. We will be in touch.")
    if body.request_type not in TICKET_REQUEST_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request_type must be contact or demo")

    ticket = CustomerTicket(
        ticket_number="PENDING",
        request_type=body.request_type,
        name=body.name.strip(),
        email=str(body.email).strip().lower(),
        phone=body.phone.strip(),
        organization=body.organization.strip(),
        message=body.message.strip(),
        status="open",
        priority="normal",
    )
    db.add(ticket)
    db.flush()
    ticket.ticket_number = f"REQ-{ticket.id:06d}"
    db.commit()
    send_customer_email(
        db,
        ticket,
        f"We received your {ticket.request_type} request",
        (
            f"Hello {ticket.name},\n\n"
            "Thank you for contacting MA-SMS. We received your request and a member "
            f"of our team will follow up shortly.\n\nReference: {ticket.ticket_number}\n\n"
            "Regards,\nMA-SMS Team"
        ),
    )
    return PublicContactOut(
        ticket_number=ticket.ticket_number,
        message="Thanks. We will be in touch shortly.",
    )


def _ticket_query():
    return select(CustomerTicket).options(selectinload(CustomerTicket.notes))


@router.get("", response_model=list[TicketOut])
def list_tickets(
    ticket_status: str | None = None,
    priority: str | None = None,
    request_type: str | None = None,
    _: StaffContext = Depends(require_permissions("tickets:read")),
    db: Session = Depends(get_db),
):
    stmt = _ticket_query().order_by(CustomerTicket.created_at.desc()).limit(200)
    if ticket_status:
        stmt = stmt.where(CustomerTicket.status == ticket_status)
    if priority:
        stmt = stmt.where(CustomerTicket.priority == priority)
    if request_type:
        stmt = stmt.where(CustomerTicket.request_type == request_type)
    return db.execute(stmt).scalars().unique().all()


@router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(
    ticket_id: int,
    _: StaffContext = Depends(require_permissions("tickets:read")),
    db: Session = Depends(get_db),
):
    ticket = db.execute(_ticket_query().where(CustomerTicket.id == ticket_id)).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


@router.patch("/{ticket_id}", response_model=TicketOut)
def update_ticket(
    ticket_id: int,
    body: TicketUpdate,
    staff: StaffContext = Depends(require_permissions("tickets:write")),
    db: Session = Depends(get_db),
):
    ticket = db.get(CustomerTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    before = {
        "status": ticket.status,
        "priority": ticket.priority,
        "assigned_to": ticket.assigned_to,
        "follow_up_at": ticket.follow_up_at.isoformat() if ticket.follow_up_at else None,
        "contacted_at": ticket.contacted_at.isoformat() if ticket.contacted_at else None,
    }
    if body.status is not None:
        if body.status not in TICKET_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {TICKET_STATUSES}")
        ticket.status = body.status
    if body.priority is not None:
        if body.priority not in TICKET_PRIORITIES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"priority must be one of {TICKET_PRIORITIES}")
        ticket.priority = body.priority
    if body.assigned_to is not None:
        ticket.assigned_to = body.assigned_to.strip()
    if body.follow_up_at is not None:
        ticket.follow_up_at = body.follow_up_at
    if body.contacted is True and ticket.contacted_at is None:
        ticket.contacted_at = datetime.now(timezone.utc)
    elif body.contacted is False:
        ticket.contacted_at = None

    record_audit(
        db,
        action="ticket.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="customer_ticket",
        target_id=ticket.ticket_number,
        reason=body.reason,
        before=before,
        after={
            "status": ticket.status,
            "priority": ticket.priority,
            "assigned_to": ticket.assigned_to,
            "follow_up_at": ticket.follow_up_at.isoformat() if ticket.follow_up_at else None,
            "contacted_at": ticket.contacted_at.isoformat() if ticket.contacted_at else None,
        },
    )
    db.commit()
    return get_ticket(ticket_id, staff, db)


@router.post("/{ticket_id}/notes", response_model=TicketNoteOut, status_code=status.HTTP_201_CREATED)
def add_ticket_note(
    ticket_id: int,
    body: TicketNoteCreate,
    staff: StaffContext = Depends(require_permissions("tickets:write")),
    db: Session = Depends(get_db),
):
    ticket = db.get(CustomerTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    note_body = body.body.strip()
    note = TicketNote(ticket_id=ticket.id, author_email=staff.email, body=note_body)
    db.add(note)
    record_audit(
        db,
        action="ticket.note.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="customer_ticket",
        target_id=ticket.ticket_number,
        after={"note": body.body.strip()},
    )
    db.commit()
    db.refresh(note)
    if body.send_email:
        send_customer_email(
            db,
            ticket,
            f"Re: {ticket.request_type.title()} request {ticket.ticket_number}",
            (
                f"Hello {ticket.name},\n\n{note_body}\n\n"
                f"Reference: {ticket.ticket_number}\n\n"
                "Regards,\nMA-SMS Team"
            ),
        )
    return note
