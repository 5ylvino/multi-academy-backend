from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.identity.permissions import PermissionSet
from app.domain.identity.roles import Realm
from app.domain.payroll.pay import (
    Allowance,
    FixedDeduction,
    PayProfile,
    PayrollSettings,
    PayRun,
    PayRunPaid,
    RunStatus,
    TaxBand,
    compute_payslip,
)
from app.domain.shared.errors import InvalidStateTransition, PermissionDenied, ValidationFailed

NOW = datetime(2026, 11, 27, tzinfo=UTC)
TENANT = uuid4()
NG = PayrollSettings.defaults(TENANT, "NGN", "NG")


def _profile(basic: int, **kw: object) -> PayProfile:
    args: dict[str, object] = {
        "tenant_id": TENANT,
        "staff_id": uuid4(),
        "basic_minor": basic,
        "allowances": [],
        "deductions": [],
        "annual_rent_minor": 0,
        "bank_name": None,
        "account_number_ciphertext": None,
        "account_hint": None,
        "tax_id": None,
        "active": True,
        "by": uuid4(),
        "now": NOW,
    }
    args.update(kw)
    return PayProfile.build(**args)  # type: ignore[arg-type]


def _run(preparer: UUID | None = None, today: date = date(2026, 11, 27)) -> PayRun:
    return PayRun.prepare(
        tenant_id=TENANT,
        year=today.year,
        month=today.month,
        currency="NGN",
        payslips=[compute_payslip(_profile(15_000_000), NG)],
        by=preparer or uuid4(),
        today=today,
        now=NOW,
    )


def test_nigeria_2026_bands() -> None:
    # NGN 800k free, 2.2M at 15%, then 18% on the next part.
    assert NG.annual_tax(80_000_000) == 0
    assert NG.annual_tax(300_000_000) == 33_000_000
    assert NG.annual_tax(329_280_000) == 33_000_000 + 5_270_400
    top = 80_000_000 + 220_000_000 + 900_000_000 + 1_300_000_000 + 2_500_000_000
    below_top = (
        220_000_000 * 15 + 900_000_000 * 18 + 1_300_000_000 * 21 + 2_500_000_000 * 23
    ) // 100
    assert NG.annual_tax(top + 100_000_000) == below_top + 25_000_000


def test_payslip_with_allowances_rent_relief_and_deductions() -> None:
    slip = compute_payslip(
        _profile(
            20_000_000,
            allowances=[
                Allowance("Housing", 8_000_000),
                Allowance("Lunch", 4_000_000, pensionable=False),
            ],
            deductions=[FixedDeduction("Loan", 1_000_000)],
            annual_rent_minor=400_000_000,  # relief capped at NGN 500k
        ),
        NG,
    )
    assert slip.gross_minor == 32_000_000
    assert slip.pension_employee_minor == 2_240_000  # 8% of 28M pensionable
    assert slip.pension_employer_minor == 2_800_000
    taxable = 32_000_000 * 12 - 2_240_000 * 12 - 50_000_000
    assert slip.taxable_annual_minor == taxable
    assert slip.paye_minor == round(NG.annual_tax(taxable) / 12)
    assert slip.net_minor == 32_000_000 - 2_240_000 - slip.paye_minor - 1_000_000
    assert slip.total_deductions_minor == slip.gross_minor - slip.net_minor


def test_minimum_wage_earners_pay_no_tax() -> None:
    assert compute_payslip(_profile(7_000_000), NG).paye_minor == 0
    assert compute_payslip(_profile(7_000_001), NG).paye_minor >= 0


def test_other_countries_start_with_no_statutory_rules() -> None:
    plain = PayrollSettings.defaults(TENANT, "GHS", "GH")
    slip = compute_payslip(_profile(10_000_000), plain)
    assert slip.net_minor == slip.gross_minor == 10_000_000


def test_deductions_above_pay_are_refused() -> None:
    with pytest.raises(ValidationFailed) as err:
        compute_payslip(_profile(1_000_000, deductions=[FixedDeduction("Loan", 2_000_000)]), NG)
    assert err.value.code == "negative_net_pay"


@pytest.mark.parametrize(
    "kw",
    [
        {"basic_minor": 0},
        {"allowances": [Allowance(" ", 100)]},
        {"allowances": [Allowance("Housing", 100), Allowance("housing", 100)]},
        {"deductions": [FixedDeduction("Loan", 0)]},
        {"annual_rent_minor": -1},
    ],
)
def test_profile_validation(kw: dict[str, object]) -> None:
    basic = kw.pop("basic_minor", 1_000_000)
    with pytest.raises(ValidationFailed):
        _profile(basic, **kw)  # type: ignore[arg-type]


def test_settings_validation() -> None:
    NG.validate()
    bad_bands = PayrollSettings(TENANT, "NGN", tax_bands=(TaxBand(None, 100), TaxBand(5, 0)))
    with pytest.raises(ValidationFailed):
        bad_bands.validate()
    with pytest.raises(ValidationFailed):
        PayrollSettings(TENANT, "NGN", pension_employee_bp=10_001).validate()
    with pytest.raises(ValidationFailed):
        PayrollSettings(TENANT, "NGN", pay_day=0).validate()


def test_two_people_approve_and_payment_is_recorded() -> None:
    preparer = uuid4()
    run = _run(preparer)
    with pytest.raises(PermissionDenied):
        run.approve(by=preparer, now=NOW)
    run.approve(by=uuid4(), now=NOW)
    with pytest.raises(InvalidStateTransition):
        run.refresh(run.payslips, by=preparer, now=NOW)
    with pytest.raises(ValidationFailed):
        run.mark_paid(by=preparer, now=NOW, reference="  ")
    run.mark_paid(by=preparer, now=NOW, reference="BATCH-1")
    assert run.status is RunStatus.PAID
    (event,) = run.pull_events()
    assert isinstance(event, PayRunPaid)
    with pytest.raises(InvalidStateTransition):
        run.void(reason="late")


def test_void_reverses_only_after_approval() -> None:
    draft = _run()
    assert draft.void(reason="Wrong month") is False
    approved = _run()
    approved.approve(by=uuid4(), now=NOW)
    with pytest.raises(ValidationFailed):
        approved.void(reason=" ")
    assert approved.void(reason="Mistake") is True


def test_runs_can_be_prepared_up_to_next_month() -> None:
    _run(today=date(2026, 12, 20))
    PayRun.prepare(
        tenant_id=TENANT,
        year=2027,
        month=1,
        currency="NGN",
        payslips=_run().payslips,
        by=uuid4(),
        today=date(2026, 12, 20),
        now=NOW,
    )
    with pytest.raises(ValidationFailed):
        PayRun.prepare(
            tenant_id=TENANT,
            year=2027,
            month=2,
            currency="NGN",
            payslips=_run().payslips,
            by=uuid4(),
            today=date(2026, 12, 20),
            now=NOW,
        )
    with pytest.raises(ValidationFailed) as err:
        PayRun.prepare(
            tenant_id=TENANT,
            year=2026,
            month=11,
            currency="NGN",
            payslips=[],
            by=uuid4(),
            today=date(2026, 11, 1),
            now=NOW,
        )
    assert err.value.code == "no_pay_profiles"


def test_preparing_pay_does_not_grant_approving_it() -> None:
    bursar = PermissionSet.for_realm(Realm.SCHOOL, frozenset({"bursar"}), frozenset())
    assert bursar.allows("payroll:manage")
    assert not bursar.allows("pay_runs:approve")
    director = PermissionSet.for_realm(Realm.SCHOOL, frozenset({"director"}), frozenset())
    assert director.allows("pay_runs:approve")
    assert not director.allows("payroll:manage")
