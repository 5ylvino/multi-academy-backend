"""Budgets and expenses."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.finance.routes import _read_capped
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.finance.spending import RecordExpenseCommand

router = APIRouter(prefix="/finance", tags=["budgets and expenses"])


class CategoryResponse(ApiModel):
    id: UUID
    name: str
    active: bool


class CategoryRequest(ApiModel):
    name: str = Field(min_length=2, max_length=80)


class UpdateCategoryRequest(ApiModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    active: bool | None = None


class SetBudgetRequest(ApiModel):
    term_id: UUID
    category_id: UUID
    planned_minor: int = Field(ge=0, le=10**13)


class BudgetLineResponse(ApiModel):
    category_id: UUID
    category_name: str
    planned_minor: int
    approved_minor: int
    pending_minor: int = Field(description="Recorded, waiting for approval.")
    remaining_minor: int
    percent_used: int | None = Field(description="Approved as a share of planned; null if none.")
    over_budget: bool


class BudgetReportResponse(ApiModel):
    term_id: UUID
    term_name: str
    currency: str
    lines: list[BudgetLineResponse]
    planned_minor: int
    approved_minor: int
    pending_minor: int
    remaining_minor: int
    unbudgeted_minor: int = Field(description="Approved spending in categories with no budget.")


class RecordExpenseRequest(ApiModel):
    category_id: UUID
    amount_minor: int = Field(gt=0, le=10**12)
    payee: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=3, max_length=500)
    spent_on: date
    method: str = Field(pattern="^(cash|bank_transfer|pos|cheque)$")
    reference: str | None = Field(default=None, max_length=120)
    term_id: UUID | None = Field(default=None, description="Defaults to the current term.")


class ApproveExpenseRequest(ApiModel):
    note: str | None = Field(default=None, max_length=500)
    over_budget_note: str | None = Field(
        default=None, max_length=500, description="Required when approval exceeds the budget."
    )


class ReasonRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


class ExpenseResponse(ApiModel):
    id: UUID
    number: str
    category_id: UUID
    term_id: UUID
    amount_minor: int
    currency: str
    payee: str
    description: str
    spent_on: date
    method: str
    reference: str | None
    status: str = Field(description="pending, approved, rejected or void")
    has_receipt: bool
    recorded_by: UUID
    recorded_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    over_budget_note: str | None
    voided_at: datetime | None
    void_reason: str | None


class ExpensePage(ApiModel):
    items: list[ExpenseResponse]
    page: PageMeta


@router.get("/expense-categories", response_model=list[CategoryResponse])
async def list_categories(
    principal: SchoolPrincipal, uc: UseCasesDep, include_inactive: bool = False
) -> list[CategoryResponse]:
    views = await uc.spending.categories(principal, include_inactive=include_inactive)
    return [CategoryResponse.model_validate(v) for v in views]


@router.post(
    "/expense-categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED
)
async def add_category(
    body: CategoryRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> CategoryResponse:
    return CategoryResponse.model_validate(
        await uc.spending.add_category(principal, name=body.name)
    )


@router.patch("/expense-categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID, body: UpdateCategoryRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> CategoryResponse:
    """Rename, retire (``active: false``) or bring back a category."""
    view = await uc.spending.update_category(
        principal, category_id, name=body.name, active=body.active
    )
    return CategoryResponse.model_validate(view)


@router.put("/budgets", response_model=BudgetLineResponse)
async def set_budget(
    body: SetBudgetRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> BudgetLineResponse:
    """Set (or change) what the school plans to spend on a category in a term."""
    view = await uc.spending.set_budget(
        principal,
        term_id=body.term_id,
        category_id=body.category_id,
        planned_minor=body.planned_minor,
    )
    return BudgetLineResponse.model_validate(view)


@router.get("/budgets", response_model=BudgetReportResponse)
async def budget_report(
    principal: SchoolPrincipal, uc: UseCasesDep, term_id: UUID | None = None
) -> BudgetReportResponse:
    """Budget against actual for a term (the current term by default)."""
    return BudgetReportResponse.model_validate(await uc.spending.report(principal, term_id=term_id))


@router.post("/expenses", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
async def record_expense(
    body: RecordExpenseRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExpenseResponse:
    """Record money the school spent. It counts once someone else approves it."""
    view = await uc.spending.record(
        principal,
        RecordExpenseCommand(
            category_id=body.category_id,
            amount_minor=body.amount_minor,
            payee=body.payee,
            description=body.description,
            spent_on=body.spent_on,
            method=body.method,
            reference=body.reference,
            term_id=body.term_id,
        ),
    )
    return ExpenseResponse.model_validate(view)


@router.get("/expenses", response_model=ExpensePage)
async def list_expenses(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    term_id: UUID | None = None,
    category_id: UUID | None = None,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(pending|approved|rejected|void)$")
    ] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ExpensePage:
    page = await uc.spending.list(
        principal,
        term_id=term_id,
        category_id=category_id,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )
    return ExpensePage(
        items=[ExpenseResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/expenses/{expense_id}", response_model=ExpenseResponse)
async def get_expense(
    expense_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExpenseResponse:
    return ExpenseResponse.model_validate(await uc.spending.get(principal, expense_id))


@router.put("/expenses/{expense_id}/receipt", response_model=ExpenseResponse)
async def attach_receipt(
    expense_id: UUID,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    file: Annotated[UploadFile, File(description="PDF, PNG, JPEG or WebP, up to 5 MB")],
) -> ExpenseResponse:
    view = await uc.spending.attach_receipt(principal, expense_id, data=await _read_capped(file))
    return ExpenseResponse.model_validate(view)


@router.get("/expenses/{expense_id}/receipt", response_class=Response)
async def download_receipt(
    expense_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> Response:
    view = await uc.spending.receipt(principal, expense_id)
    return Response(
        content=view.content,
        media_type=view.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{view.filename}"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseResponse)
async def approve_expense(
    expense_id: UUID, body: ApproveExpenseRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExpenseResponse:
    """Approve and post to the books. Over budget answers 409 ``over_budget`` unless a note
    says why."""
    view = await uc.spending.approve(
        principal, expense_id, note=body.note, over_budget_note=body.over_budget_note
    )
    return ExpenseResponse.model_validate(view)


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseResponse)
async def reject_expense(
    expense_id: UUID, body: ReasonRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExpenseResponse:
    view = await uc.spending.reject(principal, expense_id, reason=body.reason)
    return ExpenseResponse.model_validate(view)


@router.post("/expenses/{expense_id}/void", response_model=ExpenseResponse)
async def void_expense(
    expense_id: UUID, body: ReasonRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExpenseResponse:
    """Cancel a pending expense, or reverse an approved one in the books."""
    view = await uc.spending.void(principal, expense_id, reason=body.reason)
    return ExpenseResponse.model_validate(view)
