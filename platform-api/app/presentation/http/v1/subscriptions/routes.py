"""Platform billing: a school's plan and invoices, the emailed pay link, and the owner console."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from app.domain.shared.value_objects import Page
from app.presentation.http.dependencies.auth import PlatformPrincipal, SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep, client_ip
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.subscriptions.billing import InvoiceView

school_router = APIRouter(prefix="/subscription", tags=["subscription"])
public_router = APIRouter(prefix="/platform-invoices", tags=["subscription"])
platform_router = APIRouter(prefix="/platform/billing", tags=["platform billing"])
webhook_router = APIRouter(prefix="/webhooks/platform-billing", tags=["platform billing"])

Token = Annotated[str, Query(min_length=16, max_length=128, description="From the pay link.")]


class InvoiceResponse(ApiModel):
    id: UUID
    school_id: UUID
    number: str
    plan_code: str
    period_start: datetime
    period_end: datetime
    student_count: int
    currency: str
    base_minor: int
    per_student_minor: int
    total_minor: int
    status: str = Field(description="open, paid or void")
    issued_at: datetime
    due_at: datetime
    suspends_at: datetime = Field(
        description="If still unpaid at this time, the school's account is paused."
    )
    overdue: bool
    paid_at: datetime | None
    settlement_method: str | None = Field(description="online, bank_transfer or waived")
    settlement_reference: str | None
    note: str | None
    authorization_url: str | None = Field(
        description="Send the payer here to pay. Only while a recent payment page is open."
    )


class InvoicePage(ApiModel):
    items: list[InvoiceResponse]
    page: PageMeta


class SubscriptionResponse(ApiModel):
    school_id: UUID
    school_status: str
    plan_code: str
    plan_name: str
    status: str = Field(description="trialing, active, past_due or cancelled")
    is_free: bool
    currency: str
    base_price_minor: int
    price_per_student_minor: int
    billing_interval_months: int
    active_students: int
    estimated_next_minor: int = Field(description="The next invoice, if it were issued today.")
    current_period_start: datetime
    current_period_end: datetime = Field(description="When the next billing period starts.")
    trial_ends_at: datetime | None
    open_invoices: list[InvoiceResponse]
    pay_online: bool
    bank_transfer_details: str | None


class PublicInvoiceResponse(ApiModel):
    school_name: str
    invoice: InvoiceResponse
    pay_online: bool
    bank_transfer_details: str | None


class RecordPaymentRequest(ApiModel):
    method: str = Field(pattern="^(bank_transfer|waived)$", examples=["bank_transfer"])
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class VoidInvoiceRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


class ChangePlanRequest(ApiModel):
    plan_code: str = Field(min_length=1, max_length=40)


class CycleResponse(ApiModel):
    subscriptions_created: int
    invoices_issued: int
    periods_renewed: int
    reminders_sent: int
    schools_suspended: int
    payments_confirmed: int
    errors: int


def _page(page: Page[InvoiceView]) -> InvoicePage:
    return InvoicePage(
        items=[InvoiceResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


# ---- the school ------------------------------------------------------------------------


@school_router.get("", response_model=SubscriptionResponse)
async def my_subscription(principal: SchoolPrincipal, uc: UseCasesDep) -> SubscriptionResponse:
    """The school's plan, what the next invoice will be, and any invoices to pay."""
    return SubscriptionResponse.model_validate(await uc.platform_billing.subscription(principal))


@school_router.get("/invoices", response_model=InvoicePage)
async def my_invoices(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InvoicePage:
    return _page(await uc.platform_billing.invoices(principal, cursor=cursor, limit=limit))


@school_router.post("/invoices/{invoice_id}/pay", response_model=InvoiceResponse)
async def pay_invoice(
    invoice_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    """Open the payment page. Redirect to ``authorization_url``. Asking again soon after returns
    the same page."""
    return InvoiceResponse.model_validate(await uc.platform_billing.pay(principal, invoice_id))


@school_router.post("/invoices/{invoice_id}/confirm", response_model=InvoiceResponse)
async def confirm_invoice_payment(
    invoice_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    """Call on return from the payment page; checks with the provider at once."""
    return InvoiceResponse.model_validate(await uc.platform_billing.confirm(principal, invoice_id))


# ---- the emailed pay link (no sign-in; works while the school is suspended) ------------


@public_router.get("/{invoice_id}", response_model=PublicInvoiceResponse)
async def view_by_link(
    invoice_id: UUID, token: Token, request: Request, uc: UseCasesDep
) -> PublicInvoiceResponse:
    view = await uc.platform_billing.public_invoice(invoice_id, token, client_ip=client_ip(request))
    return PublicInvoiceResponse.model_validate(view)


@public_router.post("/{invoice_id}/pay", response_model=PublicInvoiceResponse)
async def pay_by_link(
    invoice_id: UUID, token: Token, request: Request, uc: UseCasesDep
) -> PublicInvoiceResponse:
    view = await uc.platform_billing.public_pay(invoice_id, token, client_ip=client_ip(request))
    return PublicInvoiceResponse.model_validate(view)


@public_router.post("/{invoice_id}/confirm", response_model=PublicInvoiceResponse)
async def confirm_by_link(
    invoice_id: UUID, token: Token, request: Request, uc: UseCasesDep
) -> PublicInvoiceResponse:
    view = await uc.platform_billing.public_confirm(invoice_id, token, client_ip=client_ip(request))
    return PublicInvoiceResponse.model_validate(view)


# ---- owner console ---------------------------------------------------------------------


@platform_router.get("/invoices", response_model=InvoicePage)
async def list_platform_invoices(
    principal: PlatformPrincipal,
    uc: UseCasesDep,
    school_id: UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(open|paid|void)$")] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InvoicePage:
    return _page(
        await uc.platform_billing.list_invoices(
            principal, tenant_id=school_id, status=status, cursor=cursor, limit=limit
        )
    )


@platform_router.get("/schools/{school_id}", response_model=SubscriptionResponse)
async def school_subscription(
    school_id: UUID, principal: PlatformPrincipal, uc: UseCasesDep
) -> SubscriptionResponse:
    view = await uc.platform_billing.school_subscription(principal, school_id)
    return SubscriptionResponse.model_validate(view)


@platform_router.put("/schools/{school_id}/plan", response_model=SubscriptionResponse)
async def change_school_plan(
    school_id: UUID, body: ChangePlanRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> SubscriptionResponse:
    """Features change now; the new price applies from the next invoice."""
    view = await uc.platform_billing.change_plan(principal, school_id, plan_code=body.plan_code)
    return SubscriptionResponse.model_validate(view)


@platform_router.post("/invoices/{invoice_id}/payments", response_model=InvoiceResponse)
async def record_invoice_payment(
    invoice_id: UUID, body: RecordPaymentRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    """Record a bank transfer (reference required) or waive the invoice (note required).
    Reinstates a school that billing suspended."""
    view = await uc.platform_billing.record_payment(
        principal, invoice_id, method=body.method, reference=body.reference, note=body.note
    )
    return InvoiceResponse.model_validate(view)


@platform_router.post("/invoices/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(
    invoice_id: UUID, body: VoidInvoiceRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    """Cancel an open invoice. The next billing run issues a fresh one for the period."""
    view = await uc.platform_billing.void(principal, invoice_id, reason=body.reason)
    return InvoiceResponse.model_validate(view)


@platform_router.post("/run", response_model=CycleResponse)
async def run_billing(principal: PlatformPrincipal, uc: UseCasesDep) -> CycleResponse:
    """Run the billing cycle now (the worker also runs it every 15 minutes)."""
    report = await uc.platform_billing.run_now(principal)
    return CycleResponse.model_validate(report)


@webhook_router.post("/paystack", include_in_schema=False)
async def platform_paystack_webhook(request: Request, uc: UseCasesDep) -> dict[str, bool]:
    """Signed notifications from the platform's own Paystack account."""
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    handled = await uc.platform_billing.handle_webhook(headers=headers, body=body)
    return {"received": True, "matched": handled}
