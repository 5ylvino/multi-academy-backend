from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.finance.payments import PaymentMethod
from app.domain.finance.spending import (
    CategorySpend,
    Expense,
    ExpenseStatus,
    category_name,
    expense_number,
)
from app.domain.shared.errors import InvalidStateTransition, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money

NOW = datetime(2026, 11, 27, 9, tzinfo=UTC)
TODAY = date(2026, 11, 27)


def _expense(**overrides: object) -> Expense:
    args: dict[str, object] = {
        "tenant_id": uuid4(),
        "number": expense_number(1, 2026),
        "category_id": uuid4(),
        "term_id": uuid4(),
        "amount": Money(50_000, "NGN"),
        "payee": "  Mama  Put ",
        "description": "Staff lunch",
        "spent_on": TODAY,
        "method": PaymentMethod.CASH,
        "reference": None,
        "by": uuid4(),
        "today": TODAY,
        "now": NOW,
    }
    args.update(overrides)
    return Expense.submit(**args)  # type: ignore[arg-type]


def test_submit_cleans_and_validates() -> None:
    e = _expense()
    assert e.payee == "Mama Put"
    assert e.number == "EXP-2026-000001"
    assert e.status is ExpenseStatus.PENDING
    for bad in (
        {"amount": Money(0, "NGN")},
        {"method": PaymentMethod.ONLINE},
        {"spent_on": TODAY + timedelta(days=1)},
        {"spent_on": TODAY - timedelta(days=400)},
        {"payee": "  "},
        {"description": "x"},
        {"method": PaymentMethod.CHEQUE, "reference": None},
    ):
        with pytest.raises(ValidationFailed):
            _expense(**bad)


def test_two_people_and_closed_states() -> None:
    recorder = uuid4()
    e = _expense(by=recorder)
    with pytest.raises(PermissionDenied):
        e.approve(by=recorder, now=NOW, note=None, over_budget_note=None)
    with pytest.raises(PermissionDenied):
        e.reject(by=recorder, now=NOW, reason="no")
    e.approve(by=uuid4(), now=NOW, note="ok", over_budget_note=None)
    with pytest.raises(InvalidStateTransition):
        e.reject(by=uuid4(), now=NOW, reason="late")
    assert e.void(by=uuid4(), now=NOW, reason="duplicate") is True
    with pytest.raises(InvalidStateTransition):
        e.void(by=uuid4(), now=NOW, reason="again")


def test_pending_void_needs_no_reversal_and_reason_required() -> None:
    e = _expense()
    with pytest.raises(ValidationFailed):
        e.void(by=uuid4(), now=NOW, reason=" ")
    assert e.void(by=uuid4(), now=NOW, reason="typo") is False


def test_category_spend_maths() -> None:
    spend = CategorySpend(
        category_id=uuid4(),
        planned=Money(1000, "NGN"),
        approved=Money(800, "NGN"),
        pending=Money(500, "NGN"),
    )
    assert spend.remaining == Money(200, "NGN")
    assert not spend.over_budget
    assert spend.would_exceed(Money(201, "NGN"))
    assert not spend.would_exceed(Money(200, "NGN"))


def test_category_name_rules() -> None:
    assert category_name("  Fuel   and  oil ") == "Fuel and oil"
    with pytest.raises(ValidationFailed):
        category_name("x")
