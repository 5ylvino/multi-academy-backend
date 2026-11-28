"""Payroll: pay settings, staff pay profiles, monthly pay runs and payslips.

The flow each month: the bursar prepares the run (a snapshot of every active pay profile), checks
it, refreshes it after any profile change, and a different person approves it. Approval freezes
the payslips and posts the cost to the ledger. The bursar then downloads the bank schedule, pays
the staff through the bank, and marks the run paid with the bank's batch reference, which posts
the payment and tells every staff member their payslip is ready.

Account numbers are encrypted at rest and only ever decrypted for the bank schedule, which is
recorded in the activity log each time it is downloaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.finance.ledger import Account, EntryKind, JournalEntry, JournalLine
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.payroll.pay import (
    Allowance,
    FixedDeduction,
    PayProfile,
    PayrollSettings,
    PayRun,
    Payslip,
    RunStatus,
    RunTotals,
    TaxBand,
    compute_payslip,
)
from app.domain.shared.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.use_cases.finance.common import load_settings as load_finance_settings
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    SchoolTime,
    Scope,
    SecretBox,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

_NOT_STAFF = frozenset({SchoolRole.STUDENT.value, SchoolRole.PARENT.value})
_ACCOUNT_NUMBER = re.compile(r"^[0-9]{6,20}$")


# ---- views ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettingsView:
    currency: str
    tax_bands: tuple[TaxBand, ...]
    tax_free_monthly_minor: int
    pension_employee_bp: int
    pension_employer_bp: int
    housing_fund_bp: int
    rent_relief_bp: int
    rent_relief_cap_minor: int
    pay_day: int
    saved: bool

    @classmethod
    def of(cls, s: PayrollSettings, *, saved: bool) -> SettingsView:
        return cls(
            currency=s.currency,
            tax_bands=s.tax_bands,
            tax_free_monthly_minor=s.tax_free_monthly_minor,
            pension_employee_bp=s.pension_employee_bp,
            pension_employer_bp=s.pension_employer_bp,
            housing_fund_bp=s.housing_fund_bp,
            rent_relief_bp=s.rent_relief_bp,
            rent_relief_cap_minor=s.rent_relief_cap_minor,
            pay_day=s.pay_day,
            saved=saved,
        )


@dataclass(frozen=True, slots=True)
class PayslipView:
    staff_id: UUID
    staff_name: str
    basic_minor: int
    allowances: tuple[Allowance, ...]
    gross_minor: int
    pension_employee_minor: int
    pension_employer_minor: int
    housing_fund_minor: int
    taxable_annual_minor: int
    paye_minor: int
    other_deductions: tuple[FixedDeduction, ...]
    total_deductions_minor: int
    net_minor: int
    bank_name: str | None
    account_hint: str | None

    @classmethod
    def of(cls, p: Payslip, name: str) -> PayslipView:
        return cls(
            staff_id=p.staff_id,
            staff_name=name,
            basic_minor=p.basic_minor,
            allowances=p.allowances,
            gross_minor=p.gross_minor,
            pension_employee_minor=p.pension_employee_minor,
            pension_employer_minor=p.pension_employer_minor,
            housing_fund_minor=p.housing_fund_minor,
            taxable_annual_minor=p.taxable_annual_minor,
            paye_minor=p.paye_minor,
            other_deductions=p.other_deductions,
            total_deductions_minor=p.total_deductions_minor,
            net_minor=p.net_minor,
            bank_name=p.bank_name,
            account_hint=p.account_hint,
        )


@dataclass(frozen=True, slots=True)
class ProfileView:
    staff_id: UUID
    staff_name: str
    basic_minor: int
    allowances: tuple[Allowance, ...]
    deductions: tuple[FixedDeduction, ...]
    annual_rent_minor: int
    bank_name: str | None
    account_hint: str | None
    tax_id: str | None
    active: bool
    updated_by: UUID
    updated_at: datetime
    #: What this profile pays under the current settings; None when the deductions are more
    #: than the pay.
    preview: PayslipView | None

    @classmethod
    def of(cls, p: PayProfile, name: str, settings: PayrollSettings) -> ProfileView:
        try:
            preview: PayslipView | None = PayslipView.of(compute_payslip(p, settings), name)
        except ValidationFailed:
            preview = None
        return cls(
            staff_id=p.staff_id,
            staff_name=name,
            basic_minor=p.basic_minor,
            allowances=p.allowances,
            deductions=p.deductions,
            annual_rent_minor=p.annual_rent_minor,
            bank_name=p.bank_name,
            account_hint=p.account_hint,
            tax_id=p.tax_id,
            active=p.active,
            updated_by=p.updated_by,
            updated_at=p.updated_at,
            preview=preview,
        )


@dataclass(frozen=True, slots=True)
class RunView:
    id: UUID
    year: int
    month: int
    currency: str
    status: str
    totals: RunTotals
    prepared_by: UUID
    prepared_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    paid_by: UUID | None
    paid_at: datetime | None
    payment_reference: str | None
    void_reason: str | None

    @classmethod
    def of(cls, run: PayRun, totals: RunTotals | None = None) -> RunView:
        return cls(
            id=run.id,
            year=run.year,
            month=run.month,
            currency=run.currency,
            status=run.status.value,
            totals=totals or run.totals,
            prepared_by=run.prepared_by,
            prepared_at=run.prepared_at,
            approved_by=run.approved_by,
            approved_at=run.approved_at,
            paid_by=run.paid_by,
            paid_at=run.paid_at,
            payment_reference=run.payment_reference,
            void_reason=run.void_reason,
        )


@dataclass(frozen=True, slots=True)
class RunDetails:
    run: RunView
    payslips: list[PayslipView]
    #: Active profiles left out because their deductions are more than their pay.
    skipped: list[UUID]


@dataclass(frozen=True, slots=True)
class MyPayslip:
    run_id: UUID
    year: int
    month: int
    currency: str
    status: str
    paid_at: datetime | None
    pay_day: int
    payslip: PayslipView


@dataclass(frozen=True, slots=True)
class BankLine:
    staff_name: str
    bank_name: str | None
    account_number: str | None
    net_minor: int


@dataclass(frozen=True, slots=True)
class BankSchedule:
    run: RunView
    lines: list[BankLine]


# ---- commands ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettingsCommand:
    tax_bands: tuple[TaxBand, ...]
    tax_free_monthly_minor: int
    pension_employee_bp: int
    pension_employer_bp: int
    housing_fund_bp: int
    rent_relief_bp: int
    rent_relief_cap_minor: int
    pay_day: int


@dataclass(frozen=True, slots=True)
class ProfileCommand:
    basic_minor: int
    allowances: tuple[Allowance, ...] = ()
    deductions: tuple[FixedDeduction, ...] = ()
    annual_rent_minor: int = 0
    bank_name: str | None = None
    #: A new account number. None keeps the saved one; clearing ``bank_name`` removes both.
    account_number: str | None = None
    tax_id: str | None = None
    active: bool = True


class Payroll:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        school_time: SchoolTime,
        box: SecretBox,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time
        self._box = box

    # ---- settings ---------------------------------------------------------------------------

    async def settings(self, actor: Principal) -> SettingsView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            saved = await uow.payroll.get_settings()
            current = saved or await _settings(uow, tenant_id)
        return SettingsView.of(current, saved=saved is not None)

    async def update_settings(self, actor: Principal, cmd: SettingsCommand) -> SettingsView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            before = await _settings(uow, tenant_id)
            after = PayrollSettings(
                tenant_id=tenant_id,
                currency=before.currency,
                tax_bands=cmd.tax_bands,
                tax_free_monthly_minor=cmd.tax_free_monthly_minor,
                pension_employee_bp=cmd.pension_employee_bp,
                pension_employer_bp=cmd.pension_employer_bp,
                housing_fund_bp=cmd.housing_fund_bp,
                rent_relief_bp=cmd.rent_relief_bp,
                rent_relief_cap_minor=cmd.rent_relief_cap_minor,
                pay_day=cmd.pay_day,
            )
            after.validate()
            await uow.payroll.save_settings(after, by=actor.user_id, now=now)
            uow.audit(
                audit.by(
                    actor,
                    "payroll.settings_updated",
                    target_type="payroll_settings",
                    target_id=tenant_id,
                    changes=audit.changes(_settings_fields(before), _settings_fields(after)),
                )
            )
            await uow.commit()
        return SettingsView.of(after, saved=True)

    # ---- profiles ---------------------------------------------------------------------------

    async def save_profile(
        self, actor: Principal, staff_id: UUID, cmd: ProfileCommand
    ) -> ProfileView:
        """Create or change a staff member's pay. Takes effect from the next run prepared or
        refreshed; approved runs never change."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            name = await _staff_name(uow, tenant_id, staff_id)
            existing = await uow.payroll.get_profile(staff_id, for_update=True)
            ciphertext, hint = self._account(cmd, existing)
            profile = PayProfile.build(
                tenant_id=tenant_id,
                staff_id=staff_id,
                basic_minor=cmd.basic_minor,
                allowances=list(cmd.allowances),
                deductions=list(cmd.deductions),
                annual_rent_minor=cmd.annual_rent_minor,
                bank_name=cmd.bank_name,
                account_number_ciphertext=ciphertext,
                account_hint=hint,
                tax_id=cmd.tax_id,
                active=cmd.active,
                by=actor.user_id,
                now=now,
            )
            if profile.bank_name is None:
                profile = replace(profile, account_number_ciphertext=None, account_hint=None)
            settings = await _settings(uow, tenant_id)
            if profile.active:
                compute_payslip(profile, settings)  # refuses deductions above pay up front
            await uow.payroll.save_profile(profile)
            uow.audit(
                audit.by(
                    actor,
                    "payroll.profile_created" if existing is None else "payroll.profile_updated",
                    target_type="school_user",
                    target_id=staff_id,
                    changes=audit.changes(
                        _profile_fields(existing) if existing else {}, _profile_fields(profile)
                    ),
                )
            )
            await uow.commit()
        return ProfileView.of(profile, name, settings)

    async def profile(self, actor: Principal, staff_id: UUID) -> ProfileView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            profile = await uow.payroll.get_profile(staff_id)
            if profile is None:
                raise NotFound("This person has no pay profile yet.")
            names = await uow.users.school_names({staff_id})
            settings = await _settings(uow, tenant_id)
        return ProfileView.of(profile, names.get(staff_id, ""), settings)

    async def profiles(
        self,
        actor: Principal,
        *,
        include_inactive: bool,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ProfileView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            page = await uow.payroll.list_profiles(
                include_inactive=include_inactive, cursor=cursor, limit=clamp_limit(limit)
            )
            names = await uow.users.school_names({p.staff_id for p in page.items})
            settings = await _settings(uow, tenant_id)
        return Page(
            items=[ProfileView.of(p, names.get(p.staff_id, ""), settings) for p in page.items],
            next_cursor=page.next_cursor,
        )

    # ---- runs -------------------------------------------------------------------------------

    async def prepare(self, actor: Principal, *, year: int, month: int) -> RunDetails:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            if await uow.payroll.open_run_for(year, month) is not None:
                raise Conflict("There is already a pay run for that month.", code="run_exists")
            settings = await _settings(uow, tenant_id)
            slips, skipped = await _payslips(uow, settings)
            run = PayRun.prepare(
                tenant_id=tenant_id,
                year=year,
                month=month,
                currency=settings.currency,
                payslips=slips,
                by=actor.user_id,
                today=today,
                now=now,
            )
            await uow.payroll.add_run(run)
            uow.audit(_run_audit(actor, "payroll.run_prepared", run, skipped=len(skipped)))
            details = await _details(uow, run, skipped)
            await uow.commit()
        return details

    async def refresh(self, actor: Principal, run_id: UUID) -> RunDetails:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            run = await _run(uow, run_id, for_update=True)
            before = run.totals
            settings = await _settings(uow, tenant_id)
            slips, skipped = await _payslips(uow, settings)
            run.refresh(slips, by=actor.user_id, now=self._clock.now())
            await uow.payroll.save_run(run, replace_payslips=True)
            uow.audit(
                _run_audit(
                    actor,
                    "payroll.run_refreshed",
                    run,
                    skipped=len(skipped),
                    before={"staff": before.staff, "net_minor": before.net_minor},
                )
            )
            details = await _details(uow, run, skipped)
            await uow.commit()
        return details

    async def approve(self, actor: Principal, run_id: UUID) -> RunView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("pay_runs:approve")
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            run = await _run(uow, run_id, for_update=True)
            run.approve(by=actor.user_id, now=now)
            await uow.payroll.save_run(run)
            await _post_accrual(uow, run, reverse=False, by=actor.user_id, now=now)
            uow.audit(_run_audit(actor, "payroll.run_approved", run))
            await uow.commit()
        return RunView.of(run)

    async def mark_paid(self, actor: Principal, run_id: UUID, *, reference: str) -> RunView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            run = await _run(uow, run_id, for_update=True)
            run.mark_paid(by=actor.user_id, now=now, reference=reference)
            await uow.payroll.save_run(run)
            net = Money(run.totals.net_minor, run.currency)
            await uow.finance.post(
                JournalEntry.post(
                    tenant_id=run.tenant_id,
                    kind=EntryKind.PAYROLL_PAID,
                    source_type="pay_run",
                    source_id=run.id,
                    memo=f"Salaries {_period(run)} paid ({run.payment_reference})",
                    lines=[
                        JournalLine(Account.SALARIES_PAYABLE, net),
                        JournalLine(Account.BANK, -net),
                    ],
                    by=actor.user_id,
                    now=now,
                )
            )
            uow.track(run)
            uow.audit(_run_audit(actor, "payroll.run_paid", run, reference=run.payment_reference))
            await uow.commit()
        return RunView.of(run)

    async def void(self, actor: Principal, run_id: UUID, *, reason: str) -> RunView:
        """Cancel a draft (preparers) or an approved run not yet paid (approvers only, and the
        ledger accrual is reversed)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            run = await _run(uow, run_id, for_update=True)
            if run.status is RunStatus.APPROVED and not actor.permissions.allows(
                "pay_runs:approve"
            ):
                raise PermissionDenied(
                    "Only an approver can cancel an approved pay run.", code="approver_only"
                )
            reverse = run.void(reason=reason)
            await uow.payroll.save_run(run)
            if reverse:
                await _post_accrual(uow, run, reverse=True, by=actor.user_id, now=now)
            uow.audit(
                _run_audit(
                    actor,
                    "payroll.run_voided",
                    run,
                    reason=run.void_reason,
                    reversed_in_ledger=reverse,
                )
            )
            await uow.commit()
        return RunView.of(run)

    async def run(self, actor: Principal, run_id: UUID) -> RunDetails:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return await _details(uow, await _run(uow, run_id), [])

    async def runs(
        self, actor: Principal, *, year: int | None, cursor: str | None, limit: int | None
    ) -> Page[RunView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.payroll.list_runs(year=year, cursor=cursor, limit=clamp_limit(limit))
        return Page(
            items=[RunView.of(item.run, item.totals) for item in page.items],
            next_cursor=page.next_cursor,
        )

    async def bank_schedule(self, actor: Principal, run_id: UUID) -> BankSchedule:
        """Net pay with full account numbers, for the bank upload. Every download is logged."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("payroll:manage")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            run = await _run(uow, run_id)
            if run.status not in {RunStatus.APPROVED, RunStatus.PAID}:
                raise Conflict(
                    "The bank schedule is ready once the run is approved.", code="not_approved"
                )
            names = await uow.users.school_names({s.staff_id for s in run.payslips})
            lines = []
            for slip in run.payslips:
                profile = await uow.payroll.get_profile(slip.staff_id)
                number = (
                    self._box.decrypt(profile.account_number_ciphertext).decode()
                    if profile is not None and profile.account_number_ciphertext
                    else None
                )
                lines.append(
                    BankLine(
                        staff_name=names.get(slip.staff_id, ""),
                        bank_name=profile.bank_name if profile else slip.bank_name,
                        account_number=number,
                        net_minor=slip.net_minor,
                    )
                )
            uow.audit(
                _run_audit(
                    actor,
                    "payroll.bank_schedule_exported",
                    run,
                    missing_accounts=sum(1 for line in lines if line.account_number is None),
                )
            )
            await uow.commit()
        lines.sort(key=lambda line: line.staff_name.lower())
        return BankSchedule(run=RunView.of(run), lines=lines)

    # ---- staff self-service -----------------------------------------------------------------

    async def my_payslips(
        self, actor: Principal, *, cursor: str | None, limit: int | None
    ) -> Page[MyPayslip]:
        """A staff member's own payslips from approved and paid runs. Needs no permission."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            page = await uow.payroll.payslips_of(
                actor.user_id, cursor=cursor, limit=clamp_limit(limit)
            )
            names = await uow.users.school_names({actor.user_id})
            settings = await _settings(uow, tenant_id)
        name = names.get(actor.user_id, "")
        return Page(
            items=[
                MyPayslip(
                    run_id=item.run.id,
                    year=item.run.year,
                    month=item.run.month,
                    currency=item.run.currency,
                    status=item.run.status.value,
                    paid_at=item.run.paid_at,
                    pay_day=settings.pay_day,
                    payslip=PayslipView.of(item.payslip, name),
                )
                for item in page.items
            ],
            next_cursor=page.next_cursor,
        )

    # ---- helpers ----------------------------------------------------------------------------

    def _account(
        self, cmd: ProfileCommand, existing: PayProfile | None
    ) -> tuple[str | None, str | None]:
        if cmd.account_number is None:
            if existing is None:
                return None, None
            return existing.account_number_ciphertext, existing.account_hint
        number = "".join(cmd.account_number.split()).replace("-", "")
        if not _ACCOUNT_NUMBER.fullmatch(number):
            raise ValidationFailed(
                "Account numbers are 6 to 20 digits.", details={"field": "account_number"}
            )
        if not (cmd.bank_name or "").strip():
            raise ValidationFailed(
                "Name the bank for this account.", details={"field": "bank_name"}
            )
        return self._box.encrypt(number.encode()), f"••••{number[-4:]}"


async def _settings(uow: UnitOfWork, tenant_id: UUID) -> PayrollSettings:
    saved = await uow.payroll.get_settings()
    if saved is not None:
        return saved
    currency = (await load_finance_settings(uow, tenant_id)).currency
    tenant = await uow.tenants.get(tenant_id)
    return PayrollSettings.defaults(tenant_id, currency, tenant.country_code if tenant else "NG")


async def _staff_name(uow: UnitOfWork, tenant_id: UUID, staff_id: UUID) -> str:
    user = await uow.users.get(staff_id)
    if (
        user is None
        or user.realm is not Realm.SCHOOL
        or user.tenant_id != tenant_id
        or not (user.roles - _NOT_STAFF)
    ):
        raise NotFound("Staff member not found.")
    return user.full_name


async def _payslips(uow: UnitOfWork, settings: PayrollSettings) -> tuple[list[Payslip], list[UUID]]:
    slips, skipped = [], []
    for profile in await uow.payroll.active_profiles():
        try:
            slips.append(compute_payslip(profile, settings))
        except ValidationFailed:
            skipped.append(profile.staff_id)
    return slips, skipped


async def _run(uow: UnitOfWork, run_id: UUID, *, for_update: bool = False) -> PayRun:
    run = await uow.payroll.get_run(run_id, for_update=for_update)
    if run is None:
        raise NotFound("Pay run not found.")
    return run


async def _details(uow: UnitOfWork, run: PayRun, skipped: list[UUID]) -> RunDetails:
    names = await uow.users.school_names({s.staff_id for s in run.payslips} | set(skipped))
    slips = sorted(
        (PayslipView.of(s, names.get(s.staff_id, "")) for s in run.payslips),
        key=lambda v: v.staff_name.lower(),
    )
    return RunDetails(run=RunView.of(run), payslips=slips, skipped=skipped)


async def _post_accrual(
    uow: UnitOfWork, run: PayRun, *, reverse: bool, by: UUID, now: datetime
) -> None:
    """Salary cost against what is now owed: net pay to staff, tax, pension, housing fund and
    the other deductions to whoever collects them."""
    t = run.totals
    sign = -1 if reverse else 1

    def m(amount: int) -> Money:
        return Money(sign * amount, run.currency)

    await uow.finance.post(
        JournalEntry.post(
            tenant_id=run.tenant_id,
            kind=EntryKind.PAYROLL_REVERSED if reverse else EntryKind.PAYROLL_ACCRUED,
            source_type="pay_run",
            source_id=run.id,
            memo=f"{'Reverse ' if reverse else ''}Salaries {_period(run)}",
            lines=[
                JournalLine(Account.SALARY_EXPENSE, m(t.gross_minor + t.pension_employer_minor)),
                JournalLine(Account.SALARIES_PAYABLE, m(-t.net_minor)),
                JournalLine(Account.PAYE_PAYABLE, m(-t.paye_minor)),
                JournalLine(
                    Account.PENSION_PAYABLE,
                    m(-(t.pension_employee_minor + t.pension_employer_minor)),
                ),
                JournalLine(Account.NHF_PAYABLE, m(-t.housing_fund_minor)),
                JournalLine(Account.OTHER_DEDUCTIONS_PAYABLE, m(-t.other_deductions_minor)),
            ],
            by=by,
            now=now,
        )
    )


def _period(run: PayRun) -> str:
    return f"{run.year}-{run.month:02d}"


def _run_audit(actor: Principal, action: str, run: PayRun, **extra: Any) -> AuditEntry:
    t = run.totals
    return audit.by(
        actor,
        action,
        target_type="pay_run",
        target_id=run.id,
        period=_period(run),
        staff=t.staff,
        gross_minor=t.gross_minor,
        net_minor=t.net_minor,
        currency=run.currency,
        **extra,
    )


def _settings_fields(s: PayrollSettings) -> dict[str, object]:
    return {
        "tax_bands": [[b.width_minor, b.rate_bp] for b in s.tax_bands],
        "tax_free_monthly_minor": s.tax_free_monthly_minor,
        "pension_employee_bp": s.pension_employee_bp,
        "pension_employer_bp": s.pension_employer_bp,
        "housing_fund_bp": s.housing_fund_bp,
        "rent_relief_bp": s.rent_relief_bp,
        "rent_relief_cap_minor": s.rent_relief_cap_minor,
        "pay_day": s.pay_day,
    }


def _profile_fields(p: PayProfile) -> dict[str, object]:
    """For the activity log: amounts and the account's last four digits, never the number."""
    return {
        "basic_minor": p.basic_minor,
        "allowances": [[a.name, a.amount_minor, a.pensionable, a.taxable] for a in p.allowances],
        "deductions": [[d.name, d.amount_minor] for d in p.deductions],
        "annual_rent_minor": p.annual_rent_minor,
        "bank_name": p.bank_name,
        "account_hint": p.account_hint,
        "tax_id": p.tax_id,
        "active": p.active,
    }
