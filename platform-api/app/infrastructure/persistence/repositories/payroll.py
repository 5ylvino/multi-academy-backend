"""Pay settings, pay profiles, pay runs and payslips."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from app.domain.payroll.repositories import RunListing, StaffPayslip
from app.domain.shared.errors import Conflict
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.payroll import (
    PayProfileRow,
    PayrollSettingsRow,
    PayRunRow,
    PayslipRow,
)
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState

_SLIP_BATCH = 500


def _allowances_json(items: tuple[Allowance, ...]) -> list[dict[str, Any]]:
    return [
        {
            "name": a.name,
            "amount_minor": a.amount_minor,
            "pensionable": a.pensionable,
            "taxable": a.taxable,
        }
        for a in items
    ]


def _allowances(data: list[dict[str, Any]]) -> tuple[Allowance, ...]:
    return tuple(
        Allowance(
            name=str(a["name"]),
            amount_minor=int(a["amount_minor"]),
            pensionable=bool(a.get("pensionable", True)),
            taxable=bool(a.get("taxable", True)),
        )
        for a in data
    )


def _deductions_json(items: tuple[FixedDeduction, ...]) -> list[dict[str, Any]]:
    return [{"name": d.name, "amount_minor": d.amount_minor} for d in items]


def _deductions(data: list[dict[str, Any]]) -> tuple[FixedDeduction, ...]:
    return tuple(FixedDeduction(str(d["name"]), int(d["amount_minor"])) for d in data)


def _settings(r: PayrollSettingsRow) -> PayrollSettings:
    return PayrollSettings(
        tenant_id=r.tenant_id,
        currency=r.currency,
        tax_bands=tuple(
            TaxBand(
                width_minor=None if b.get("width_minor") is None else int(b["width_minor"]),
                rate_bp=int(b["rate_bp"]),
            )
            for b in r.tax_bands
        ),
        tax_free_monthly_minor=r.tax_free_monthly_minor,
        pension_employee_bp=r.pension_employee_bp,
        pension_employer_bp=r.pension_employer_bp,
        housing_fund_bp=r.housing_fund_bp,
        rent_relief_bp=r.rent_relief_bp,
        rent_relief_cap_minor=r.rent_relief_cap_minor,
        pay_day=r.pay_day,
    )


def _profile(r: PayProfileRow) -> PayProfile:
    return PayProfile(
        id=r.staff_id,
        tenant_id=r.tenant_id,
        basic_minor=r.basic_minor,
        allowances=_allowances(r.allowances),
        deductions=_deductions(r.deductions),
        annual_rent_minor=r.annual_rent_minor,
        bank_name=r.bank_name,
        account_number_ciphertext=r.account_number_ciphertext,
        account_hint=r.account_hint,
        tax_id=r.tax_id,
        active=r.active,
        updated_by=r.updated_by,
        updated_at=r.updated_at,
    )


def _payslip(r: PayslipRow) -> Payslip:
    return Payslip(
        staff_id=r.staff_id,
        basic_minor=r.basic_minor,
        allowances=_allowances(r.allowances),
        gross_minor=r.gross_minor,
        pension_employee_minor=r.pension_employee_minor,
        pension_employer_minor=r.pension_employer_minor,
        housing_fund_minor=r.housing_fund_minor,
        taxable_annual_minor=r.taxable_annual_minor,
        paye_minor=r.paye_minor,
        other_deductions=_deductions(r.other_deductions),
        net_minor=r.net_minor,
        bank_name=r.bank_name,
        account_hint=r.account_hint,
    )


def _run(r: PayRunRow, payslips: list[Payslip] | None = None) -> PayRun:
    return PayRun(
        id=r.id,
        tenant_id=r.tenant_id,
        year=r.year,
        month=r.month,
        currency=r.currency,
        status=RunStatus(r.status),
        prepared_by=r.prepared_by,
        prepared_at=r.prepared_at,
        payslips=payslips or [],
        approved_by=r.approved_by,
        approved_at=r.approved_at,
        paid_by=r.paid_by,
        paid_at=r.paid_at,
        payment_reference=r.payment_reference,
        void_reason=r.void_reason,
    )


def _totals(r: PayRunRow) -> RunTotals:
    return RunTotals(
        staff=r.staff_count,
        gross_minor=r.gross_minor,
        net_minor=r.net_minor,
        paye_minor=r.paye_minor,
        pension_employee_minor=r.pension_employee_minor,
        pension_employer_minor=r.pension_employer_minor,
        housing_fund_minor=r.housing_fund_minor,
        other_deductions_minor=r.other_deductions_minor,
    )


def _run_values(run: PayRun) -> dict[str, Any]:
    t = run.totals
    return {
        "status": run.status.value,
        "staff_count": t.staff,
        "gross_minor": t.gross_minor,
        "net_minor": t.net_minor,
        "paye_minor": t.paye_minor,
        "pension_employee_minor": t.pension_employee_minor,
        "pension_employer_minor": t.pension_employer_minor,
        "housing_fund_minor": t.housing_fund_minor,
        "other_deductions_minor": t.other_deductions_minor,
        "prepared_by": run.prepared_by,
        "prepared_at": run.prepared_at,
        "approved_by": run.approved_by,
        "approved_at": run.approved_at,
        "paid_by": run.paid_by,
        "paid_at": run.paid_at,
        "payment_reference": run.payment_reference,
        "void_reason": run.void_reason,
    }


class SqlPayrollRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Payroll needs a tenant scope.")
        return self._scope.tenant_id

    # ---- settings ---------------------------------------------------------------------------

    async def get_settings(self) -> PayrollSettings | None:
        row = await self._s.get(PayrollSettingsRow, self._tid)
        return _settings(row) if row else None

    async def save_settings(self, settings: PayrollSettings, *, by: UUID, now: datetime) -> None:
        values: dict[str, Any] = {
            "currency": settings.currency,
            "tax_bands": [
                {"width_minor": b.width_minor, "rate_bp": b.rate_bp} for b in settings.tax_bands
            ],
            "tax_free_monthly_minor": settings.tax_free_monthly_minor,
            "pension_employee_bp": settings.pension_employee_bp,
            "pension_employer_bp": settings.pension_employer_bp,
            "housing_fund_bp": settings.housing_fund_bp,
            "rent_relief_bp": settings.rent_relief_bp,
            "rent_relief_cap_minor": settings.rent_relief_cap_minor,
            "pay_day": settings.pay_day,
            "updated_by": by,
            "updated_at": now,
        }
        stmt = pg_insert(PayrollSettingsRow).values(tenant_id=self._tid, **values)
        await self._s.execute(
            stmt.on_conflict_do_update(index_elements=[PayrollSettingsRow.tenant_id], set_=values)
        )

    # ---- profiles ---------------------------------------------------------------------------

    async def get_profile(self, staff_id: UUID, *, for_update: bool = False) -> PayProfile | None:
        stmt = select(PayProfileRow).where(
            PayProfileRow.tenant_id == self._tid, PayProfileRow.staff_id == staff_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _profile(row) if row else None

    async def save_profile(self, profile: PayProfile) -> None:
        values: dict[str, Any] = {
            "basic_minor": profile.basic_minor,
            "allowances": _allowances_json(profile.allowances),
            "deductions": _deductions_json(profile.deductions),
            "annual_rent_minor": profile.annual_rent_minor,
            "bank_name": profile.bank_name,
            "account_number_ciphertext": profile.account_number_ciphertext,
            "account_hint": profile.account_hint,
            "tax_id": profile.tax_id,
            "active": profile.active,
            "updated_by": profile.updated_by,
            "updated_at": profile.updated_at,
        }
        stmt = pg_insert(PayProfileRow).values(
            tenant_id=self._tid, staff_id=profile.staff_id, **values
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[PayProfileRow.tenant_id, PayProfileRow.staff_id], set_=values
            )
        )

    async def list_profiles(
        self, *, include_inactive: bool, cursor: str | None, limit: int
    ) -> Page[PayProfile]:
        stmt = select(PayProfileRow).where(PayProfileRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(PayProfileRow.active.is_(True))
        if cursor:
            stmt = stmt.where(PayProfileRow.staff_id > _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(PayProfileRow.staff_id).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_profile(r) for r in rows],
            next_cursor=_encode(rows[-1].staff_id) if more and rows else None,
        )

    async def active_profiles(self) -> list[PayProfile]:
        rows = await self._s.scalars(
            select(PayProfileRow)
            .where(PayProfileRow.tenant_id == self._tid, PayProfileRow.active.is_(True))
            .order_by(PayProfileRow.staff_id)
        )
        return [_profile(r) for r in rows]

    # ---- runs -------------------------------------------------------------------------------

    async def add_run(self, run: PayRun) -> None:
        try:
            await self._s.execute(
                pg_insert(PayRunRow).values(
                    tenant_id=self._tid,
                    id=run.id,
                    year=run.year,
                    month=run.month,
                    currency=run.currency,
                    **_run_values(run),
                )
            )
        except IntegrityError as exc:
            if "uq_payroll_runs_month" in str(exc.orig):
                raise Conflict(
                    "There is already a pay run for that month.", code="run_exists"
                ) from exc
            raise
        await self._insert_payslips(run)

    async def get_run(self, run_id: UUID, *, for_update: bool = False) -> PayRun | None:
        stmt = select(PayRunRow).where(PayRunRow.tenant_id == self._tid, PayRunRow.id == run_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        if row is None:
            return None
        slips = await self._s.scalars(
            select(PayslipRow)
            .where(PayslipRow.tenant_id == self._tid, PayslipRow.run_id == run_id)
            .order_by(PayslipRow.staff_id)
        )
        return _run(row, [_payslip(s) for s in slips])

    async def save_run(self, run: PayRun, *, replace_payslips: bool = False) -> None:
        if replace_payslips:
            await self._s.execute(
                delete(PayslipRow).where(
                    PayslipRow.tenant_id == self._tid, PayslipRow.run_id == run.id
                )
            )
            await self._insert_payslips(run)
        await self._s.execute(
            update(PayRunRow)
            .where(PayRunRow.tenant_id == self._tid, PayRunRow.id == run.id)
            .values(**_run_values(run))
        )

    async def open_run_for(self, year: int, month: int) -> UUID | None:
        return await self._s.scalar(
            select(PayRunRow.id).where(
                PayRunRow.tenant_id == self._tid,
                PayRunRow.year == year,
                PayRunRow.month == month,
                PayRunRow.status != RunStatus.VOID.value,
            )
        )

    async def list_runs(
        self, *, year: int | None, cursor: str | None, limit: int
    ) -> Page[RunListing]:
        stmt = select(PayRunRow).where(PayRunRow.tenant_id == self._tid)
        if year is not None:
            stmt = stmt.where(PayRunRow.year == year)
        if cursor:
            stmt = stmt.where(PayRunRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(PayRunRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[RunListing(run=_run(r), totals=_totals(r)) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def payslips_of(
        self, staff_id: UUID, *, cursor: str | None, limit: int
    ) -> Page[StaffPayslip]:
        stmt = (
            select(PayslipRow, PayRunRow)
            .join(
                PayRunRow,
                (PayRunRow.tenant_id == PayslipRow.tenant_id) & (PayRunRow.id == PayslipRow.run_id),
            )
            .where(
                PayslipRow.tenant_id == self._tid,
                PayslipRow.staff_id == staff_id,
                PayRunRow.status.in_([RunStatus.APPROVED.value, RunStatus.PAID.value]),
            )
        )
        if cursor:
            stmt = stmt.where(PayRunRow.id < _decode(cursor))
        rows = list(
            (await self._s.execute(stmt.order_by(PayRunRow.id.desc()).limit(limit + 1))).all()
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[StaffPayslip(run=_run(run), payslip=_payslip(slip)) for slip, run in rows],
            next_cursor=_encode(rows[-1][1].id) if more and rows else None,
        )

    async def _insert_payslips(self, run: PayRun) -> None:
        rows = [
            {
                "tenant_id": self._tid,
                "run_id": run.id,
                "staff_id": p.staff_id,
                "basic_minor": p.basic_minor,
                "allowances": _allowances_json(p.allowances),
                "gross_minor": p.gross_minor,
                "pension_employee_minor": p.pension_employee_minor,
                "pension_employer_minor": p.pension_employer_minor,
                "housing_fund_minor": p.housing_fund_minor,
                "taxable_annual_minor": p.taxable_annual_minor,
                "paye_minor": p.paye_minor,
                "other_deductions": _deductions_json(p.other_deductions),
                "net_minor": p.net_minor,
                "bank_name": p.bank_name,
                "account_hint": p.account_hint,
            }
            for p in run.payslips
        ]
        for start in range(0, len(rows), _SLIP_BATCH):
            await self._s.execute(pg_insert(PayslipRow).values(rows[start : start + _SLIP_BATCH]))
