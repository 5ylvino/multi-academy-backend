"""Payroll: settings, staff pay profiles, monthly pay runs, bank schedules and payslips."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import Field

from app.domain.payroll.pay import Allowance, FixedDeduction, TaxBand
from app.domain.shared.money import minor_digits
from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.payroll.runs import ProfileCommand, SettingsCommand

router = APIRouter(prefix="/payroll", tags=["payroll"])

Minor = Annotated[int, Field(ge=0, le=10**13)]
BasisPoints = Annotated[int, Field(ge=0, le=10_000, description="Hundredths of a percent.")]
Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]


# ---- schemas -------------------------------------------------------------------------------


class TaxBandModel(ApiModel):
    width_minor: int | None = Field(
        ge=1, le=10**14, description="Annual income in this band; null for the top band."
    )
    rate_bp: BasisPoints


class SettingsRequest(ApiModel):
    tax_bands: list[TaxBandModel] = Field(max_length=20)
    tax_free_monthly_minor: Minor = Field(
        description="Monthly gross at or below this pays no income tax."
    )
    pension_employee_bp: BasisPoints
    pension_employer_bp: BasisPoints
    housing_fund_bp: BasisPoints
    rent_relief_bp: BasisPoints
    rent_relief_cap_minor: Minor
    pay_day: int = Field(ge=1, le=31)


class SettingsResponse(SettingsRequest):
    currency: str
    saved: bool = Field(description="False while the school still uses the country defaults.")


class AllowanceModel(ApiModel):
    name: str = Field(min_length=1, max_length=60)
    amount_minor: int = Field(gt=0, le=10**11)
    pensionable: bool = True
    taxable: bool = True


class DeductionModel(ApiModel):
    name: str = Field(min_length=1, max_length=60)
    amount_minor: int = Field(gt=0, le=10**11)


class ProfileRequest(ApiModel):
    basic_minor: int = Field(gt=0, le=10**11, description="Monthly basic pay.")
    allowances: list[AllowanceModel] = Field(default_factory=list, max_length=30)
    deductions: list[DeductionModel] = Field(
        default_factory=list, max_length=30, description="Fixed monthly: loans, cooperative."
    )
    annual_rent_minor: Minor = Field(default=0, description="For rent relief on income tax.")
    bank_name: str | None = Field(default=None, max_length=80)
    account_number: str | None = Field(
        default=None,
        max_length=30,
        description="Leave out to keep the saved number. Clearing bank_name removes both.",
    )
    tax_id: str | None = Field(default=None, max_length=40)
    active: bool = True


class PayslipResponse(ApiModel):
    staff_id: UUID
    staff_name: str
    basic_minor: int
    allowances: list[AllowanceModel]
    gross_minor: int
    pension_employee_minor: int
    pension_employer_minor: int = Field(description="Paid by the school, not deducted.")
    housing_fund_minor: int
    taxable_annual_minor: int
    paye_minor: int
    other_deductions: list[DeductionModel]
    total_deductions_minor: int
    net_minor: int
    bank_name: str | None
    account_hint: str | None


class ProfileResponse(ApiModel):
    staff_id: UUID
    staff_name: str
    basic_minor: int
    allowances: list[AllowanceModel]
    deductions: list[DeductionModel]
    annual_rent_minor: int
    bank_name: str | None
    account_hint: str | None = Field(description="Last four digits only.")
    tax_id: str | None
    active: bool
    updated_by: UUID
    updated_at: datetime
    preview: PayslipResponse | None = Field(
        description="This month's pay under the current settings."
    )


class ProfilePage(ApiModel):
    items: list[ProfileResponse]
    page: PageMeta


class TotalsResponse(ApiModel):
    staff: int
    gross_minor: int
    net_minor: int
    paye_minor: int
    pension_employee_minor: int
    pension_employer_minor: int
    housing_fund_minor: int
    other_deductions_minor: int


class RunResponse(ApiModel):
    id: UUID
    year: int
    month: int
    currency: str
    status: str = Field(description="draft, approved, paid or void")
    totals: TotalsResponse
    prepared_by: UUID
    prepared_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    paid_by: UUID | None
    paid_at: datetime | None
    payment_reference: str | None
    void_reason: str | None


class RunPage(ApiModel):
    items: list[RunResponse]
    page: PageMeta


class RunDetailsResponse(ApiModel):
    run: RunResponse
    payslips: list[PayslipResponse]
    skipped: list[UUID] = Field(
        description="Staff left out because their deductions are more than their pay."
    )


class PrepareRequest(ApiModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class PaidRequest(ApiModel):
    reference: str = Field(min_length=1, max_length=120, description="Bank batch reference.")


class VoidRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=300)


class MyPayslipResponse(ApiModel):
    run_id: UUID
    year: int
    month: int
    currency: str
    status: str
    paid_at: datetime | None
    pay_day: int
    payslip: PayslipResponse


class MyPayslipPage(ApiModel):
    items: list[MyPayslipResponse]
    page: PageMeta


# ---- settings ------------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(principal: SchoolPrincipal, uc: UseCasesDep) -> SettingsResponse:
    """Tax bands, pension and housing fund rates. Nigerian schools start from the 2026 rules."""
    return SettingsResponse.model_validate(await uc.payroll.settings(principal))


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SettingsResponse:
    """Applies to runs prepared or recalculated from now; approved runs keep their figures."""
    view = await uc.payroll.update_settings(
        principal,
        SettingsCommand(
            tax_bands=tuple(TaxBand(b.width_minor, b.rate_bp) for b in body.tax_bands),
            tax_free_monthly_minor=body.tax_free_monthly_minor,
            pension_employee_bp=body.pension_employee_bp,
            pension_employer_bp=body.pension_employer_bp,
            housing_fund_bp=body.housing_fund_bp,
            rent_relief_bp=body.rent_relief_bp,
            rent_relief_cap_minor=body.rent_relief_cap_minor,
            pay_day=body.pay_day,
        ),
    )
    return SettingsResponse.model_validate(view)


# ---- profiles ------------------------------------------------------------------------------


@router.get("/profiles", response_model=ProfilePage)
async def list_profiles(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    include_inactive: bool = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ProfilePage:
    page = await uc.payroll.profiles(
        principal, include_inactive=include_inactive, cursor=cursor, limit=limit
    )
    return ProfilePage(
        items=[ProfileResponse.model_validate(p) for p in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/profiles/{staff_id}", response_model=ProfileResponse)
async def get_profile(
    staff_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProfileResponse:
    return ProfileResponse.model_validate(await uc.payroll.profile(principal, staff_id))


@router.put("/profiles/{staff_id}", response_model=ProfileResponse)
async def save_profile(
    staff_id: UUID, body: ProfileRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProfileResponse:
    """Set up or change one staff member's pay. The response previews the monthly payslip."""
    view = await uc.payroll.save_profile(
        principal,
        staff_id,
        ProfileCommand(
            basic_minor=body.basic_minor,
            allowances=tuple(
                Allowance(a.name, a.amount_minor, a.pensionable, a.taxable) for a in body.allowances
            ),
            deductions=tuple(FixedDeduction(d.name, d.amount_minor) for d in body.deductions),
            annual_rent_minor=body.annual_rent_minor,
            bank_name=body.bank_name,
            account_number=body.account_number,
            tax_id=body.tax_id,
            active=body.active,
        ),
    )
    return ProfileResponse.model_validate(view)


# ---- runs ----------------------------------------------------------------------------------


@router.get("/runs", response_model=RunPage)
async def list_runs(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    cursor: Cursor = None,
    limit: Limit = 24,
) -> RunPage:
    page = await uc.payroll.runs(principal, year=year, cursor=cursor, limit=limit)
    return RunPage(
        items=[RunResponse.model_validate(r) for r in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.post("/runs", response_model=RunDetailsResponse, status_code=status.HTTP_201_CREATED)
async def prepare_run(
    body: PrepareRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RunDetailsResponse:
    """Work out every active staff member's payslip for the month, as a draft to check."""
    details = await uc.payroll.prepare(principal, year=body.year, month=body.month)
    return RunDetailsResponse.model_validate(details)


@router.get("/runs/{run_id}", response_model=RunDetailsResponse)
async def get_run(run_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> RunDetailsResponse:
    return RunDetailsResponse.model_validate(await uc.payroll.run(principal, run_id))


@router.post("/runs/{run_id}/refresh", response_model=RunDetailsResponse)
async def refresh_run(
    run_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> RunDetailsResponse:
    """Recalculate a draft after changing pay profiles or settings."""
    return RunDetailsResponse.model_validate(await uc.payroll.refresh(principal, run_id))


@router.post("/runs/{run_id}/approve", response_model=RunResponse)
async def approve_run(run_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> RunResponse:
    """By someone other than the preparer. Freezes the payslips and posts the salary cost."""
    return RunResponse.model_validate(await uc.payroll.approve(principal, run_id))


@router.post("/runs/{run_id}/paid", response_model=RunResponse)
async def mark_run_paid(
    run_id: UUID, body: PaidRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RunResponse:
    """After the bank has paid the staff. Staff are emailed that their payslip is ready."""
    view = await uc.payroll.mark_paid(principal, run_id, reference=body.reference)
    return RunResponse.model_validate(view)


@router.post("/runs/{run_id}/void", response_model=RunResponse)
async def void_run(
    run_id: UUID, body: VoidRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RunResponse:
    """Cancel a draft, or (approvers) an approved run that hasn't been paid."""
    return RunResponse.model_validate(await uc.payroll.void(principal, run_id, reason=body.reason))


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _amount(minor: int, currency: str) -> str:
    """Plain decimal for bank uploads: 150000.50, no separators."""
    digits = minor_digits(currency)
    if digits == 0:
        return str(minor)
    major, rest = divmod(minor, 10**digits)
    return f"{major}.{rest:0{digits}d}"


def _cell(value: str | None) -> str:
    text = value or ""
    return "'" + text if text.startswith(_FORMULA_START) else text


@router.get(
    "/runs/{run_id}/bank-schedule.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def bank_schedule(run_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> Response:
    """Name, bank, account number and net pay for the bank's bulk payment upload. Each download
    is recorded in the activity log."""
    schedule = await uc.payroll.bank_schedule(principal, run_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "bank", "account_number", "amount", "currency", "narration"])
    run = schedule.run
    narration = f"Salary {run.year}-{run.month:02d}"
    for line in schedule.lines:
        writer.writerow(
            [
                _cell(line.staff_name),
                _cell(line.bank_name),
                line.account_number or "",
                _amount(line.net_minor, run.currency),
                run.currency,
                narration,
            ]
        )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="salaries-{run.year}-{run.month:02d}.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )


# ---- my payslips ---------------------------------------------------------------------------


@router.get("/my-payslips", response_model=MyPayslipPage)
async def my_payslips(
    principal: SchoolPrincipal, uc: UseCasesDep, cursor: Cursor = None, limit: Limit = 12
) -> MyPayslipPage:
    """The signed-in staff member's own payslips, newest first."""
    page = await uc.payroll.my_payslips(principal, cursor=cursor, limit=limit)
    return MyPayslipPage(
        items=[MyPayslipResponse.model_validate(p) for p in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )
