"""School fees request and response models. Amounts are integer minor units (kobo, cents)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from masms.presentation.http.v1.schemas import ApiModel, PageMeta

MAX_MINOR = 10**12

LevelName = Literal["nursery", "primary", "secondary"]
TermName = Literal["first", "second", "third"]
ManualMethod = Literal["cash", "bank_transfer", "pos", "cheque"]


class MoneyModel(ApiModel):
    amount_minor: int
    currency: str


class SettingsResponse(ApiModel):
    currency: str
    default_due_days: int
    currency_locked: bool


class SettingsRequest(ApiModel):
    currency: str = Field(min_length=3, max_length=3)
    default_due_days: int = Field(ge=0, le=180)


class FeeItemRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    amount_minor: int = Field(gt=0, le=MAX_MINOR)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    school_level: LevelName | None = None
    class_id: UUID | None = None
    terms: list[TermName] = Field(default_factory=list, max_length=3)
    mandatory: bool = True


class FeeItemResponse(ApiModel):
    id: UUID
    name: str
    amount: MoneyModel
    school_level: str | None
    class_id: UUID | None
    terms: list[str]
    mandatory: bool
    active: bool
    created_at: datetime


class ScholarshipRequest(ApiModel):
    student_id: UUID
    session_id: UUID | None = Field(default=None, description="Defaults to the current session")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["percent", "fixed"]
    value: int = Field(
        gt=0, le=MAX_MINOR, description="Basis points for percent (5000 = 50%), else minor units"
    )


class ScholarshipResponse(ApiModel):
    id: UUID
    student_id: UUID
    session_id: UUID
    name: str
    kind: str
    value: int
    currency: str
    active: bool
    granted_by: UUID
    granted_at: datetime
    revoked_at: datetime | None


class BillingRunRequest(ApiModel):
    term_id: UUID | None = Field(default=None, description="Defaults to the current term")
    class_ids: list[UUID] | None = Field(default=None, max_length=500)
    due_on: date | None = None


class BillingRunResponse(ApiModel):
    term_id: UUID
    invoiced: int
    already_billed: int
    nothing_to_bill: int
    total_billed: MoneyModel


class InvoiceRequest(ApiModel):
    student_id: UUID
    fee_item_ids: list[UUID] = Field(min_length=1, max_length=50)
    due_on: date | None = None


class LineModel(ApiModel):
    description: str
    amount: MoneyModel
    fee_item_id: UUID | None


class DiscountModel(ApiModel):
    description: str
    amount: MoneyModel
    scholarship_id: UUID | None


class InvoiceResponse(ApiModel):
    id: UUID
    number: str
    kind: str
    status: str
    student_id: UUID
    class_id: UUID | None
    session_id: UUID | None
    term_id: UUID | None
    lines: list[LineModel]
    discounts: list[DiscountModel]
    subtotal: MoneyModel
    discount_total: MoneyModel
    total: MoneyModel
    amount_paid: MoneyModel
    balance: MoneyModel
    due_on: date
    issued_at: datetime
    voided_at: datetime | None
    void_reason: str | None


class InvoicePage(ApiModel):
    items: list[InvoiceResponse]
    page: PageMeta


class ReasonRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


class PaymentRequest(ApiModel):
    student_id: UUID
    amount_minor: int = Field(gt=0, le=MAX_MINOR)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    method: ManualMethod
    reference: str | None = Field(default=None, max_length=120)
    received_on: date | None = None


class AllocationModel(ApiModel):
    invoice_id: UUID
    amount: MoneyModel


class PaymentResponse(ApiModel):
    id: UUID
    receipt_number: str
    student_id: UUID
    amount: MoneyModel
    method: str
    reference: str | None
    received_on: date
    status: str
    allocated: MoneyModel
    refunded: MoneyModel
    unused_credit: MoneyModel
    recorded_by: UUID
    recorded_at: datetime
    reversed_at: datetime | None
    reversal_reason: str | None
    allocations: list[AllocationModel]


class PaymentPage(ApiModel):
    items: list[PaymentResponse]
    page: PageMeta


class RefundRequest(ApiModel):
    amount_minor: int = Field(gt=0, le=MAX_MINOR)
    method: ManualMethod
    reason: str = Field(min_length=3, max_length=500)


class DecisionRequest(ApiModel):
    note: str | None = Field(default=None, max_length=500)


class RefundResponse(ApiModel):
    id: UUID
    payment_id: UUID
    student_id: UUID
    amount: MoneyModel
    method: str
    reason: str
    status: str
    requested_by: UUID
    requested_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None


class StatementLineModel(ApiModel):
    at: datetime
    kind: str
    reference: str
    description: str
    charge_minor: int
    credit_minor: int
    balance_minor: int


class StatementResponse(ApiModel):
    student_id: UUID
    full_name: str
    currency: str
    invoiced: MoneyModel
    paid: MoneyModel
    refunded: MoneyModel
    owed: MoneyModel
    credit: MoneyModel
    net_due: MoneyModel
    lines: list[StatementLineModel]


class TrialBalanceRowModel(ApiModel):
    account: str
    currency: str
    balance_minor: int


class TrialBalanceResponse(ApiModel):
    rows: list[TrialBalanceRowModel]
    balanced: bool
