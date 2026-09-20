from app.models import CustomerTicket
from app.routers.tickets import create_public_contact, update_ticket
from app.schemas import PublicContactCreate, TicketUpdate


def test_public_contact_creates_reference_and_defaults(session):
    result = create_public_contact(
        PublicContactCreate(
            name="Ada School",
            email="hello@adaschool.ng",
            phone="08000000000",
            organization="Ada Academy",
            request_type="demo",
            message="We would like to see the platform.",
        ),
        session,
    )

    ticket = session.query(CustomerTicket).one()
    assert result.ticket_number == "REQ-000001"
    assert ticket.status == "open"
    assert ticket.priority == "normal"
    assert ticket.request_type == "demo"


def test_ticket_update_records_follow_up_and_contacted(session):
    create_public_contact(
        PublicContactCreate(
            name="Bola",
            email="bola@example.com",
            request_type="contact",
            message="Please contact me about setup options.",
        ),
        session,
    )
    ticket = session.query(CustomerTicket).one()
    staff = type("Staff", (), {"id": "1", "email": "operator@example.com"})()

    updated = update_ticket(
        ticket.id,
        TicketUpdate(status="in_progress", priority="high", contacted=True),
        staff,
        session,
    )

    assert updated.status == "in_progress"
    assert updated.priority == "high"
    assert updated.contacted_at is not None
    assert session.query(CustomerTicket).one().assigned_to == ""
