"""School fees domain rules: money, invoices, settlement, refunds and the ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.finance.billing import (
    Invoice,
    InvoiceKind,
    InvoiceLine,
    InvoiceStatus,
    Scholarship,
    ScholarshipKind,
)
from app.domain.finance.catalog import FeeItem, FinanceSettings
from app.domain.finance.ledger import Account, EntryKind, JournalEntry, JournalLine
from app.domain.finance.payments import (
    Payment,
    PaymentMethod,
    Refund,
    RefundStatus,
    parse_method,
    plan_settlement,
)
from app.domain.shared.errors import (
    InvalidStateTransition,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.money import Money, default_currency

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
TENANT = uuid4()
STUDENT = uuid4()
STAFF = uuid4()


def ngn(minor: int) -> Money:
    return Money(minor, "NGN")


def _invoice(
    *amounts: int, due: date | None = None, scholarships: list[Scholarship] | None = None
) -> Invoice:
    return Invoice.issue(
        tenant_id=TENANT,
        number=f"INV-{uuid4().hex[:6]}",
        kind=InvoiceKind.ADHOC,
        student_id=STUDENT,
        class_id=None,
        session_id=None,
        term_id=None,
        currency="NGN",
        lines=[InvoiceLine(description=f"Item {a}", amount=ngn(a)) for a in amounts],
        scholarships=scholarships or [],
        due_on=due or TODAY + timedelta(days=14),
        today=TODAY,
        by=STAFF,
        now=NOW,
    )


def _payment(minor: int, *, received: date = TODAY) -> Payment:
    return Payment.receive(
        tenant_id=TENANT,
        receipt_number=f"RCT-{uuid4().hex[:6]}",
        student_id=STUDENT,
        amount=ngn(minor),
        method=PaymentMethod.CASH,
        reference=None,
        received_on=received,
        by=STAFF,
        now=NOW,
    )


# ---- money -------------------------------------------------------------------------------


def test_money_refuses_floats_mixed_currencies_and_huge_amounts() -> None:
    with pytest.raises(ValidationFailed):
        Money(10.5, "NGN")  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        Money(True, "NGN")
    with pytest.raises(ValidationFailed):
        Money(10**13, "NGN")
    with pytest.raises(ValidationFailed):
        ngn(1) + Money(1, "USD")
    with pytest.raises(ValidationFailed):
        Money(1, "naira")
    assert Money(1, "ngn").currency == "NGN"


def test_share_rounds_half_up_and_str_formats() -> None:
    assert ngn(1_001).share(5_000) == ngn(501)  # 500.5 rounds up
    assert ngn(1_000).share(3_333) == ngn(333)
    assert ngn(-1_001).share(5_000) == ngn(-501)
    with pytest.raises(ValidationFailed):
        ngn(1).share(10_001)
    assert str(ngn(1_234_567)) == "NGN 12,345.67"
    assert str(ngn(-5)) == "NGN -0.05"
    assert str(Money(5000, "UGX")) == "UGX 5,000"
    assert default_currency("ng") == "NGN"
    assert default_currency("zz") == "USD"


# ---- catalog -----------------------------------------------------------------------------


def test_fee_item_scope_and_terms() -> None:
    class_id = uuid4()
    tuition = FeeItem.create(
        tenant_id=TENANT,
        name="  Tuition  ",
        amount=ngn(5_000_000),
        school_level="primary",
        class_id=None,
        terms=frozenset({"first"}),
        mandatory=True,
        now=NOW,
    )
    assert tuition.name == "Tuition"
    assert tuition.applies_to(class_id=class_id, school_level="primary", term="first")
    assert not tuition.applies_to(class_id=class_id, school_level="primary", term="second")
    assert not tuition.applies_to(class_id=class_id, school_level="secondary", term="first")
    tuition.active = False
    assert not tuition.applies_to(class_id=class_id, school_level="primary", term="first")

    with pytest.raises(ValidationFailed):
        FeeItem.create(
            tenant_id=TENANT,
            name="Both",
            amount=ngn(1),
            school_level="primary",
            class_id=class_id,
            terms=frozenset(),
            mandatory=True,
            now=NOW,
        )
    with pytest.raises(ValidationFailed):
        FeeItem.create(
            tenant_id=TENANT,
            name="Zero",
            amount=ngn(0),
            school_level=None,
            class_id=None,
            terms=frozenset(),
            mandatory=True,
            now=NOW,
        )


def test_settings_currency_locks_once_invoices_exist() -> None:
    settings = FinanceSettings.defaults(TENANT, "NGN")
    settings.update(currency="ghs", default_due_days=30, currency_locked=False)
    assert settings.currency == "GHS"
    with pytest.raises(ValidationFailed) as err:
        settings.update(currency="NGN", default_due_days=30, currency_locked=True)
    assert err.value.code == "currency_locked"
    with pytest.raises(ValidationFailed):
        settings.update(currency="GHS", default_due_days=181, currency_locked=True)


# ---- invoices and scholarships -----------------------------------------------------------


def _scholarship(kind: ScholarshipKind, value: int) -> Scholarship:
    return Scholarship.grant(
        tenant_id=TENANT,
        student_id=STUDENT,
        session_id=uuid4(),
        name="Merit",
        kind=kind,
        value=value,
        currency="NGN",
        by=STAFF,
        now=NOW,
    )


def test_scholarships_discount_and_never_exceed_the_subtotal() -> None:
    half = _scholarship(ScholarshipKind.PERCENT, 5_000)
    big_fixed = _scholarship(ScholarshipKind.FIXED, 900_000)
    invoice = _invoice(600_000, 400_000, scholarships=[half, big_fixed])
    assert invoice.subtotal == ngn(1_000_000)
    assert [d.amount for d in invoice.discounts] == [ngn(500_000), ngn(500_000)]
    assert invoice.total == ngn(0)
    assert invoice.status is InvoiceStatus.PAID
    assert not invoice.is_open

    with pytest.raises(ValidationFailed):
        _scholarship(ScholarshipKind.PERCENT, 10_001)
    with pytest.raises(ValidationFailed):
        _scholarship(ScholarshipKind.FIXED, 0)
    revoked = _scholarship(ScholarshipKind.PERCENT, 1_000)
    revoked.revoke(by=STAFF, now=NOW)
    with pytest.raises(InvalidStateTransition):
        revoked.revoke(by=STAFF, now=NOW)
    assert _invoice(1_000, scholarships=[revoked]).discounts == []


def test_invoice_rules() -> None:
    with pytest.raises(ValidationFailed):
        _invoice()
    with pytest.raises(ValidationFailed):
        _invoice(100, due=TODAY - timedelta(days=1))
    invoice = _invoice(1_000)
    events = invoice.pull_events()
    assert events[0].name == "finance.invoice_issued"

    invoice.apply(ngn(400))
    assert invoice.status is InvoiceStatus.PARTIALLY_PAID
    with pytest.raises(ValidationFailed):
        invoice.apply(ngn(601))
    invoice.apply(ngn(600))
    assert invoice.status is InvoiceStatus.PAID
    assert invoice.balance.is_zero
    with pytest.raises(InvalidStateTransition):
        invoice.apply(ngn(1))
    invoice.release(ngn(600))
    assert invoice.status is InvoiceStatus.PARTIALLY_PAID
    with pytest.raises(ValidationFailed):
        invoice.release(ngn(401))

    released = invoice.void(reason="Billed the wrong class", by=STAFF, now=NOW)
    assert released == ngn(400)
    assert invoice.status is InvoiceStatus.VOID
    assert invoice.balance.is_zero
    with pytest.raises(InvalidStateTransition):
        invoice.void(reason="again", by=STAFF, now=NOW)
    with pytest.raises(ValidationFailed):
        _invoice(1).void(reason="  ", by=STAFF, now=NOW)


# ---- payments and settlement -------------------------------------------------------------


def test_payment_rules() -> None:
    with pytest.raises(ValidationFailed):
        _payment(0)
    with pytest.raises(ValidationFailed):
        _payment(100, received=TODAY + timedelta(days=1))
    with pytest.raises(ValidationFailed):
        Payment.receive(
            tenant_id=TENANT,
            receipt_number="R",
            student_id=STUDENT,
            amount=ngn(100),
            method=PaymentMethod.BANK_TRANSFER,
            reference="   ",
            received_on=TODAY,
            by=STAFF,
            now=NOW,
        )
    with pytest.raises(ValidationFailed):
        parse_method("online")
    assert parse_method("online", manual=False) is PaymentMethod.ONLINE
    with pytest.raises(ValidationFailed):
        parse_method("crypto")


def test_settlement_pays_oldest_due_first_and_keeps_the_rest_as_credit() -> None:
    later = _invoice(5_000, due=TODAY + timedelta(days=30))
    sooner = _invoice(3_000, due=TODAY + timedelta(days=7))
    first, second = _payment(2_000, received=TODAY - timedelta(days=2)), _payment(10_000)

    plan = plan_settlement([second, first], [later, sooner])
    assert [(p.id, i.id, a) for p, i, a in plan] == [
        (first.id, sooner.id, ngn(2_000)),
        (second.id, sooner.id, ngn(1_000)),
        (second.id, later.id, ngn(5_000)),
    ]
    for payment, invoice, amount in plan:
        payment.allocate(amount)
        invoice.apply(amount)
    assert sooner.status is later.status is InvoiceStatus.PAID
    assert second.available == ngn(4_000)
    assert plan_settlement([first, second], [later, sooner]) == []


def test_reversal_needs_released_allocations_and_no_refunds() -> None:
    payment = _payment(1_000)
    payment.allocate(ngn(600))
    with pytest.raises(InvalidStateTransition):
        payment.reverse(reason="Cheque bounced", by=STAFF, now=NOW)
    payment.deallocate(ngn(600))
    payment.hold(ngn(100))
    with pytest.raises(InvalidStateTransition):
        payment.reverse(reason="Cheque bounced", by=STAFF, now=NOW)
    payment.release_hold(ngn(100))
    payment.reverse(reason="Cheque bounced", by=STAFF, now=NOW)
    assert payment.available.is_zero
    with pytest.raises(InvalidStateTransition):
        payment.reverse(reason="Cheque bounced", by=STAFF, now=NOW)


def test_refunds_need_a_second_person_and_unused_credit() -> None:
    payment = _payment(1_000)
    payment.allocate(ngn(700))
    with pytest.raises(ValidationFailed) as err:
        Refund.request(
            payment=payment,
            amount=ngn(301),
            method=PaymentMethod.CASH,
            reason="Overpaid",
            by=STAFF,
            now=NOW,
        )
    assert err.value.code == "insufficient_credit"
    refund = Refund.request(
        payment=payment,
        amount=ngn(300),
        method=PaymentMethod.BANK_TRANSFER,
        reason="Overpaid",
        by=STAFF,
        now=NOW,
    )
    assert payment.available.is_zero  # held while waiting
    with pytest.raises(PermissionDenied) as denied:
        refund.approve(payment, by=STAFF, now=NOW, note=None)
    assert denied.value.code == "dual_control_required"
    refund.approve(payment, by=uuid4(), now=NOW, note="ok")
    assert refund.status is RefundStatus.APPROVED
    assert payment.refunded == ngn(300)
    assert payment.held.is_zero
    with pytest.raises(InvalidStateTransition):
        refund.reject(payment, by=uuid4(), now=NOW, note=None)

    other = _payment(500)
    rejected = Refund.request(
        payment=other, amount=ngn(500), method=PaymentMethod.CASH, reason="Asked", by=STAFF, now=NOW
    )
    rejected.reject(other, by=uuid4(), now=NOW, note="Not eligible")
    assert other.available == ngn(500)
    with pytest.raises(ValidationFailed):
        Refund.request(
            payment=other,
            amount=ngn(1),
            method=PaymentMethod.ONLINE,
            reason="Asked",
            by=STAFF,
            now=NOW,
        )


# ---- ledger ------------------------------------------------------------------------------


def test_journal_entries_must_balance_and_tag_students_correctly() -> None:
    def post(lines: list[JournalLine]) -> JournalEntry:
        return JournalEntry.post(
            tenant_id=TENANT,
            kind=EntryKind.PAYMENT_RECORDED,
            source_type="payment",
            source_id=uuid4(),
            memo="x",
            lines=lines,
            by=STAFF,
            now=NOW,
        )

    entry = post(
        [
            JournalLine(Account.CASH, ngn(500)),
            JournalLine(Account.STUDENT_CREDIT, ngn(-500), STUDENT),
            JournalLine(Account.DISCOUNTS, ngn(0)),
        ]
    )
    assert len(entry.lines) == 2  # zero lines dropped
    with pytest.raises(ValidationFailed) as err:
        post([JournalLine(Account.CASH, ngn(500)), JournalLine(Account.FEE_INCOME, ngn(-499))])
    assert err.value.code == "unbalanced_entry"
    with pytest.raises(ValidationFailed):
        post([JournalLine(Account.CASH, ngn(5)), JournalLine(Account.RECEIVABLE, ngn(-5))])
    with pytest.raises(ValidationFailed):
        post([JournalLine(Account.CASH, ngn(5)), JournalLine(Account.FEE_INCOME, ngn(-5), STUDENT)])
    with pytest.raises(ValidationFailed):
        post([JournalLine(Account.CASH, ngn(5))])
