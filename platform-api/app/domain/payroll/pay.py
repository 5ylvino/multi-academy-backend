"""Staff pay and monthly pay runs.

Each staff member has a pay profile: monthly basic pay, allowances and fixed deductions (a loan
repayment, cooperative savings). A pay run for a month takes a snapshot of every active profile
and works out each payslip: gross, pension, housing fund, income tax (PAYE) and net pay.

Statutory rules are school settings, not code, because they differ by country and change by law.
Nigerian schools start from the Nigeria Tax Act 2025 (in force from January 2026): annual bands
0% on the first NGN 800,000, then 15%, 18%, 21%, 23% and 25%; pension 8% employee and 10%
employer; housing fund 2.5% of basic when the school enrols; rent relief 20% of annual rent up to
NGN 500,000; no PAYE at or below the minimum wage. A school (or its accountant) can change any of
them.

One person prepares a run and a different person approves it. Approval posts the cost to the
ledger (salary expense against salaries and deductions payable); marking it paid moves net pay
out of the bank. Payslips never change after approval; a mistake is fixed in the next run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import InvalidStateTransition, PermissionDenied, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id

BASIS_POINTS = 10_000
MAX_MONTHLY_MINOR = 10**11  # NGN 1 billion a month: a typo guard, not a policy


def _round_half_up(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder * 2 >= denominator else 0)


def _share(amount: int, basis_points: int) -> int:
    return _round_half_up(amount * basis_points, BASIS_POINTS)


def _clean(text: str | None, limit: int) -> str | None:
    value = " ".join((text or "").split())
    return value[:limit] or None


# ---- statutory settings --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaxBand:
    """``width_minor`` of annual taxable income taxed at ``rate_bp``; None means the rest."""

    width_minor: int | None
    rate_bp: int


NIGERIA_2026_BANDS = (
    TaxBand(80_000_000, 0),
    TaxBand(220_000_000, 1_500),
    TaxBand(900_000_000, 1_800),
    TaxBand(1_300_000_000, 2_100),
    TaxBand(2_500_000_000, 2_300),
    TaxBand(None, 2_500),
)


@dataclass(frozen=True, slots=True)
class PayrollSettings:
    tenant_id: UUID
    currency: str
    tax_bands: tuple[TaxBand, ...] = ()
    #: Monthly gross at or below this pays no PAYE (the minimum wage exemption).
    tax_free_monthly_minor: int = 0
    pension_employee_bp: int = 0
    pension_employer_bp: int = 0
    housing_fund_bp: int = 0
    rent_relief_bp: int = 0
    rent_relief_cap_minor: int = 0
    #: Day of the month staff are usually paid; shown on payslips.
    pay_day: int = 25

    @classmethod
    def defaults(cls, tenant_id: UUID, currency: str, country_code: str) -> PayrollSettings:
        if country_code.upper() == "NG":
            return cls(
                tenant_id=tenant_id,
                currency=currency,
                tax_bands=NIGERIA_2026_BANDS,
                tax_free_monthly_minor=7_000_000,
                pension_employee_bp=800,
                pension_employer_bp=1_000,
                housing_fund_bp=0,  # on only for schools enrolled in the NHF
                rent_relief_bp=2_000,
                rent_relief_cap_minor=50_000_000,
            )
        return cls(tenant_id=tenant_id, currency=currency)

    def validate(self) -> None:
        for name in (
            "pension_employee_bp",
            "pension_employer_bp",
            "housing_fund_bp",
            "rent_relief_bp",
        ):
            if not 0 <= getattr(self, name) <= BASIS_POINTS:
                raise ValidationFailed("Rates are 0-100%.", details={"field": name})
        if self.tax_free_monthly_minor < 0 or self.rent_relief_cap_minor < 0:
            raise ValidationFailed("Amounts can't be negative.")
        if not 1 <= self.pay_day <= 31:
            raise ValidationFailed("Pay day is 1-31.", details={"field": "pay_day"})
        for i, band in enumerate(self.tax_bands):
            last = i == len(self.tax_bands) - 1
            if not 0 <= band.rate_bp <= BASIS_POINTS:
                raise ValidationFailed("Tax rates are 0-100%.", details={"field": "tax_bands"})
            if (band.width_minor is None) != last or (
                band.width_minor is not None and band.width_minor <= 0
            ):
                raise ValidationFailed(
                    "Every band but the last needs a width above zero; the last has none.",
                    details={"field": "tax_bands"},
                )

    def annual_tax(self, taxable_minor: int) -> int:
        remaining, tax = max(taxable_minor, 0), 0
        for band in self.tax_bands:
            portion = remaining if band.width_minor is None else min(remaining, band.width_minor)
            tax += portion * band.rate_bp
            remaining -= portion
            if remaining <= 0:
                break
        return _round_half_up(tax, BASIS_POINTS)


# ---- pay profiles --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Allowance:
    name: str
    amount_minor: int
    pensionable: bool = True  # housing and transport usually are
    taxable: bool = True


@dataclass(frozen=True, slots=True)
class FixedDeduction:
    name: str
    amount_minor: int


def _items_named(names: list[str]) -> None:
    lowered = [n.lower() for n in names]
    if len(set(lowered)) != len(lowered):
        raise ValidationFailed("Each allowance and deduction needs its own name.")


@dataclass(kw_only=True, eq=False)
class PayProfile:
    """One per staff member. ``id`` is the staff member's user id."""

    id: UUID
    tenant_id: UUID
    basic_minor: int
    allowances: tuple[Allowance, ...]
    deductions: tuple[FixedDeduction, ...]
    annual_rent_minor: int
    bank_name: str | None
    account_number_ciphertext: str | None
    account_hint: str | None
    tax_id: str | None
    active: bool
    updated_by: UUID
    updated_at: datetime

    @classmethod
    def build(
        cls,
        *,
        tenant_id: UUID,
        staff_id: UUID,
        basic_minor: int,
        allowances: list[Allowance],
        deductions: list[FixedDeduction],
        annual_rent_minor: int,
        bank_name: str | None,
        account_number_ciphertext: str | None,
        account_hint: str | None,
        tax_id: str | None,
        active: bool,
        by: UUID,
        now: datetime,
    ) -> PayProfile:
        if not 0 < basic_minor <= MAX_MONTHLY_MINOR:
            raise ValidationFailed("Enter the monthly basic pay.", details={"field": "basic_minor"})
        clean_allowances = []
        for a in allowances:
            name = _clean(a.name, 60)
            if name is None or not 0 < a.amount_minor <= MAX_MONTHLY_MINOR:
                raise ValidationFailed(
                    "Each allowance needs a name and an amount.", details={"field": "allowances"}
                )
            clean_allowances.append(Allowance(name, a.amount_minor, a.pensionable, a.taxable))
        clean_deductions = []
        for d in deductions:
            name = _clean(d.name, 60)
            if name is None or not 0 < d.amount_minor <= MAX_MONTHLY_MINOR:
                raise ValidationFailed(
                    "Each deduction needs a name and an amount.", details={"field": "deductions"}
                )
            clean_deductions.append(FixedDeduction(name, d.amount_minor))
        _items_named([a.name for a in clean_allowances] + [d.name for d in clean_deductions])
        if annual_rent_minor < 0:
            raise ValidationFailed(
                "Rent can't be negative.", details={"field": "annual_rent_minor"}
            )
        return cls(
            id=staff_id,
            tenant_id=tenant_id,
            basic_minor=basic_minor,
            allowances=tuple(clean_allowances),
            deductions=tuple(clean_deductions),
            annual_rent_minor=annual_rent_minor,
            bank_name=_clean(bank_name, 80),
            account_number_ciphertext=account_number_ciphertext,
            account_hint=account_hint,
            tax_id=_clean(tax_id, 40),
            active=active,
            updated_by=by,
            updated_at=now,
        )

    @property
    def staff_id(self) -> UUID:
        return self.id


# ---- payslips ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Payslip:
    """What one person is paid in one run. Frozen once the run is approved."""

    staff_id: UUID
    basic_minor: int
    allowances: tuple[Allowance, ...]
    gross_minor: int
    pension_employee_minor: int
    pension_employer_minor: int
    housing_fund_minor: int
    taxable_annual_minor: int
    paye_minor: int
    other_deductions: tuple[FixedDeduction, ...]
    net_minor: int
    bank_name: str | None
    account_hint: str | None

    @property
    def total_deductions_minor(self) -> int:
        return (
            self.pension_employee_minor
            + self.housing_fund_minor
            + self.paye_minor
            + sum(d.amount_minor for d in self.other_deductions)
        )


def compute_payslip(profile: PayProfile, settings: PayrollSettings) -> Payslip:
    allowances = profile.allowances
    gross = profile.basic_minor + sum(a.amount_minor for a in allowances)
    pensionable = profile.basic_minor + sum(a.amount_minor for a in allowances if a.pensionable)
    pension = _share(pensionable, settings.pension_employee_bp)
    employer_pension = _share(pensionable, settings.pension_employer_bp)
    housing = _share(profile.basic_minor, settings.housing_fund_bp)
    taxable_monthly = profile.basic_minor + sum(a.amount_minor for a in allowances if a.taxable)
    rent_relief = min(
        _share(profile.annual_rent_minor, settings.rent_relief_bp), settings.rent_relief_cap_minor
    )
    taxable_annual = max(taxable_monthly * 12 - (pension + housing) * 12 - rent_relief, 0)
    if gross <= settings.tax_free_monthly_minor:
        paye = 0
    else:
        paye = _round_half_up(settings.annual_tax(taxable_annual), 12)
    others = profile.deductions
    net = gross - pension - housing - paye - sum(d.amount_minor for d in others)
    if net < 0:
        raise ValidationFailed(
            "Deductions are more than this person's pay.",
            code="negative_net_pay",
            details={"staff_id": str(profile.staff_id)},
        )
    return Payslip(
        staff_id=profile.staff_id,
        basic_minor=profile.basic_minor,
        allowances=allowances,
        gross_minor=gross,
        pension_employee_minor=pension,
        pension_employer_minor=employer_pension,
        housing_fund_minor=housing,
        taxable_annual_minor=taxable_annual,
        paye_minor=paye,
        other_deductions=others,
        net_minor=net,
        bank_name=profile.bank_name,
        account_hint=profile.account_hint,
    )


# ---- pay runs ------------------------------------------------------------------------------


class RunStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    PAID = "paid"
    VOID = "void"


@dataclass(frozen=True, kw_only=True)
class PayRunPaid(DomainEvent):
    name: ClassVar[str] = "payroll.run_paid"
    run_id: UUID
    year: int
    month: int
    staff_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunTotals:
    staff: int
    gross_minor: int
    net_minor: int
    paye_minor: int
    pension_employee_minor: int
    pension_employer_minor: int
    housing_fund_minor: int
    other_deductions_minor: int

    @classmethod
    def of(cls, slips: list[Payslip]) -> RunTotals:
        return cls(
            staff=len(slips),
            gross_minor=sum(s.gross_minor for s in slips),
            net_minor=sum(s.net_minor for s in slips),
            paye_minor=sum(s.paye_minor for s in slips),
            pension_employee_minor=sum(s.pension_employee_minor for s in slips),
            pension_employer_minor=sum(s.pension_employer_minor for s in slips),
            housing_fund_minor=sum(s.housing_fund_minor for s in slips),
            other_deductions_minor=sum(d.amount_minor for s in slips for d in s.other_deductions),
        )


@dataclass(kw_only=True, eq=False)
class PayRun(AggregateRoot):
    tenant_id: UUID
    year: int
    month: int
    currency: str
    status: RunStatus
    prepared_by: UUID
    prepared_at: datetime
    payslips: list[Payslip] = field(default_factory=list)
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    paid_by: UUID | None = None
    paid_at: datetime | None = None
    payment_reference: str | None = None
    void_reason: str | None = None

    @classmethod
    def prepare(
        cls,
        *,
        tenant_id: UUID,
        year: int,
        month: int,
        currency: str,
        payslips: list[Payslip],
        by: UUID,
        today: date,
        now: datetime,
    ) -> PayRun:
        if not 1 <= month <= 12 or not 2000 <= year <= 2100:
            raise ValidationFailed("Pick a valid month.", details={"field": "month"})
        latest = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        if (year, month) > latest:
            raise ValidationFailed(
                "You can prepare this month or next month at the latest.",
                details={"field": "month"},
            )
        if not payslips:
            raise ValidationFailed(
                "No staff have an active pay profile yet.", code="no_pay_profiles"
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            year=year,
            month=month,
            currency=currency,
            status=RunStatus.DRAFT,
            prepared_by=by,
            prepared_at=now,
            payslips=payslips,
        )

    @property
    def totals(self) -> RunTotals:
        return RunTotals.of(self.payslips)

    def refresh(self, payslips: list[Payslip], *, by: UUID, now: datetime) -> None:
        """Recalculate a draft after profiles changed."""
        self._ensure(RunStatus.DRAFT)
        if not payslips:
            raise ValidationFailed(
                "No staff have an active pay profile yet.", code="no_pay_profiles"
            )
        self.payslips = payslips
        self.prepared_by = by
        self.prepared_at = now

    def approve(self, *, by: UUID, now: datetime) -> None:
        self._ensure(RunStatus.DRAFT)
        if by == self.prepared_by:
            raise PermissionDenied(
                "Someone else must approve a pay run you prepared.", code="same_person"
            )
        self.status = RunStatus.APPROVED
        self.approved_by = by
        self.approved_at = now

    def mark_paid(self, *, by: UUID, now: datetime, reference: str) -> None:
        self._ensure(RunStatus.APPROVED)
        clean = _clean(reference, 120)
        if clean is None:
            raise ValidationFailed(
                "Enter the bank batch or transfer reference.", details={"field": "reference"}
            )
        self.status = RunStatus.PAID
        self.paid_by = by
        self.paid_at = now
        self.payment_reference = clean
        self.record(
            PayRunPaid(
                tenant_id=self.tenant_id,
                run_id=self.id,
                year=self.year,
                month=self.month,
                staff_ids=tuple(str(s.staff_id) for s in self.payslips),
            )
        )

    def void(self, *, reason: str) -> bool:
        """Cancel a draft or an unpaid approved run. Returns True when the ledger needs the
        accrual reversed."""
        if self.status not in {RunStatus.DRAFT, RunStatus.APPROVED}:
            raise InvalidStateTransition(f"This pay run is {self.status.value}.", code="run_closed")
        clean = _clean(reason, 300)
        if clean is None:
            raise ValidationFailed("Say why it is cancelled.", details={"field": "reason"})
        accrued = self.status is RunStatus.APPROVED
        self.status = RunStatus.VOID
        self.void_reason = clean
        return accrued

    def _ensure(self, status: RunStatus) -> None:
        if self.status is not status:
            raise InvalidStateTransition(f"This pay run is {self.status.value}.", code="run_closed")
