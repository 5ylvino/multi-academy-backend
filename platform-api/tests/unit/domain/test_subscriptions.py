from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.subscriptions.billing import (
    GRACE_PERIOD,
    REMIND_EVERY,
    REMIND_WINDOW,
    InvoiceStatus,
    PlatformInvoice,
    PlatformInvoicePaid,
    SettlementMethod,
    Subscription,
    SubscriptionStatus,
    add_months,
    invoice_number,
    new_checkout_reference,
)

NOW = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)


def _invoice(**overrides: object) -> PlatformInvoice:
    args: dict[str, object] = {
        "tenant_id": uuid4(),
        "number": invoice_number(7, 2026),
        "plan_code": "standard",
        "currency": "NGN",
        "base_minor": 5_000_000,
        "per_student_minor": 250_000,
        "student_count": 120,
        "period_start": NOW + timedelta(days=7),
        "period_end": add_months(NOW + timedelta(days=7), 4),
        "now": NOW,
    }
    args.update(overrides)
    return PlatformInvoice.issue(**args)  # type: ignore[arg-type]


def test_add_months_clamps_to_month_end() -> None:
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 15, tzinfo=UTC), 4) == datetime(2027, 3, 15, tzinfo=UTC)
    assert add_months(datetime(2028, 2, 29, tzinfo=UTC), 12) == datetime(2029, 2, 28, tzinfo=UTC)


def test_numbers_and_references() -> None:
    assert invoice_number(42, 2026) == "MAS-2026-000042"
    ref = new_checkout_reference()
    assert ref.startswith("MASP-")
    assert len(ref) == 25
    assert ref != new_checkout_reference()


def test_invoice_total_and_due_date() -> None:
    invoice = _invoice()
    assert invoice.total.amount_minor == 5_000_000 + 120 * 250_000
    assert invoice.due_at == invoice.period_start
    assert invoice.suspends_at == invoice.due_at + GRACE_PERIOD
    [event] = invoice.pull_events()
    assert event.payload()["due_at"] == invoice.due_at.isoformat()


def test_late_invoice_is_due_when_issued_not_in_the_past() -> None:
    invoice = _invoice(period_start=NOW - timedelta(days=3), period_end=add_months(NOW, 4))
    assert invoice.due_at == NOW


def test_bad_period_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        _invoice(period_end=NOW)


def test_reminders_every_few_days_within_the_window() -> None:
    invoice = _invoice()
    due = invoice.due_at
    assert not invoice.should_remind(due)
    assert invoice.should_remind(due + timedelta(hours=1))
    invoice.remind(due + timedelta(hours=1))
    assert not invoice.should_remind(due + timedelta(days=1))
    assert invoice.should_remind(due + timedelta(hours=1) + REMIND_EVERY)
    assert not invoice.should_remind(due + REMIND_WINDOW + timedelta(days=1))


def test_settle_once_and_waiver_needs_a_reason() -> None:
    invoice = _invoice()
    invoice.pull_events()
    with pytest.raises(ValidationFailed):
        invoice.settle(method=SettlementMethod.WAIVED, reference=None, by=None, now=NOW)
    assert invoice.settle(
        method=SettlementMethod.BANK_TRANSFER, reference=" TRF-1 ", by=uuid4(), now=NOW
    )
    assert invoice.status is InvoiceStatus.PAID
    assert invoice.settlement_reference == "TRF-1"
    assert not invoice.is_overdue(invoice.due_at + timedelta(days=30))
    assert isinstance(invoice.pull_events()[0], PlatformInvoicePaid)
    assert not invoice.settle(method=SettlementMethod.ONLINE, reference="x", by=None, now=NOW)
    with pytest.raises(InvalidStateTransition):
        invoice.void("changed mind", by=uuid4(), now=NOW)


def test_void_needs_a_reason_and_blocks_payment() -> None:
    invoice = _invoice()
    with pytest.raises(ValidationFailed):
        invoice.void("  ", by=uuid4(), now=NOW)
    invoice.void("Agreed a discount", by=uuid4(), now=NOW)
    assert invoice.status is InvoiceStatus.VOID
    with pytest.raises(InvalidStateTransition):
        invoice.start_checkout("MASP-X", "https://pay", NOW)
    with pytest.raises(InvalidStateTransition):
        invoice.settle(method=SettlementMethod.ONLINE, reference="x", by=None, now=NOW)


def test_subscription_periods_only_move_forward() -> None:
    sub = Subscription.create(
        tenant_id=uuid4(),
        plan_code="standard",
        trialing=True,
        started=NOW,
        first_bill_at=NOW + timedelta(days=30),
        now=NOW,
    )
    assert sub.status is SubscriptionStatus.TRIALING
    assert sub.mark_past_due(NOW)
    assert not sub.mark_past_due(NOW)
    start = NOW + timedelta(days=30)
    sub.begin_period(start, add_months(start, 4), NOW)
    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.current_period_end == add_months(start, 4)
    sub.begin_period(NOW, NOW + timedelta(days=1), NOW)  # an older invoice paid late
    assert sub.current_period_end == add_months(start, 4)
