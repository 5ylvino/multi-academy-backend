"""Online fee payments: the school's provider connection, checkouts, and provider webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta

router = APIRouter(prefix="/finance", tags=["online payments"])
webhook_router = APIRouter(prefix="/webhooks/payments", tags=["online payments"])


class ConnectGatewayRequest(ApiModel):
    provider: str = Field(default="paystack", max_length=24, examples=["paystack"])
    public_key: str = Field(min_length=10, max_length=200)
    secret_key: str = Field(min_length=10, max_length=200)


class GatewayResponse(ApiModel):
    provider: str
    mode: str = Field(description="live or test")
    public_key: str
    key_hint: str = Field(description="Last 4 characters of the secret key.")
    connected_at: datetime
    webhook_url: str = Field(
        description="Paste this into the provider dashboard (Paystack: Settings > API Keys & "
        "Webhooks > Live Webhook URL)."
    )


class GatewayStatusResponse(ApiModel):
    connected: bool
    gateway: GatewayResponse | None
    providers: list[str] = Field(description="Providers this platform supports.")


class StartCheckoutRequest(ApiModel):
    student_id: UUID
    amount_minor: int | None = Field(
        default=None, gt=0, le=10**12, description="Leave empty to pay everything owed."
    )


class CheckoutResponse(ApiModel):
    id: UUID
    reference: str
    provider: str
    status: str = Field(description="pending, succeeded, failed, abandoned or needs_review")
    student_id: UUID
    amount_minor: int
    currency: str
    paid_minor: int | None
    authorization_url: str | None = Field(
        description="Send the payer here to pay. Only while pending."
    )
    payment_id: UUID | None
    failure_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class CheckoutPage(ApiModel):
    items: list[CheckoutResponse]
    page: PageMeta


@router.get("/payment-gateway", response_model=GatewayStatusResponse)
async def gateway_status(principal: SchoolPrincipal, uc: UseCasesDep) -> GatewayStatusResponse:
    """Whether the school has connected its own payment provider account."""
    view = await uc.online_payments.gateway(principal)
    return GatewayStatusResponse(
        connected=view is not None,
        gateway=GatewayResponse.model_validate(view) if view else None,
        providers=uc.online_payments.providers(),
    )


@router.put("/payment-gateway", response_model=GatewayResponse)
async def connect_gateway(
    body: ConnectGatewayRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> GatewayResponse:
    """Connect (or replace) the school's provider account. The keys are checked with the
    provider first; the secret key is stored encrypted and never shown again. Then turn on
    online payments in the finance settings."""
    view = await uc.online_payments.connect(
        principal, provider=body.provider, public_key=body.public_key, secret_key=body.secret_key
    )
    return GatewayResponse.model_validate(view)


@router.delete("/payment-gateway", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_gateway(principal: SchoolPrincipal, uc: UseCasesDep) -> Response:
    """Stop taking online payments now. Payments already made still complete."""
    await uc.online_payments.disconnect(principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/checkouts", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED)
async def start_checkout(
    body: StartCheckoutRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> CheckoutResponse:
    """Start paying a child's fees online. Redirect the payer to ``authorization_url``."""
    view = await uc.online_payments.start_checkout(
        principal, student_id=body.student_id, amount_minor=body.amount_minor
    )
    return CheckoutResponse.model_validate(view)


@router.get("/checkouts", response_model=CheckoutPage)
async def list_checkouts(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CheckoutPage:
    page = await uc.online_payments.list(
        principal, student_id=student_id, cursor=cursor, limit=limit
    )
    return CheckoutPage(
        items=[CheckoutResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/checkouts/{checkout_id}", response_model=CheckoutResponse)
async def get_checkout(
    checkout_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> CheckoutResponse:
    return CheckoutResponse.model_validate(await uc.online_payments.get(principal, checkout_id))


@router.post("/checkouts/{checkout_id}/confirm", response_model=CheckoutResponse)
async def confirm_checkout(
    checkout_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> CheckoutResponse:
    """Call when the payer returns from the payment page. Checks with the provider at once,
    so the receipt shows without waiting for the webhook. Safe to call repeatedly."""
    return CheckoutResponse.model_validate(await uc.online_payments.confirm(principal, checkout_id))


@webhook_router.post("/{provider}/{school_id}", include_in_schema=False)
async def provider_webhook(
    provider: str, school_id: UUID, request: Request, uc: UseCasesDep
) -> dict[str, bool]:
    """Signed notifications from the provider. A bad signature gets 401; anything else that
    is ours is confirmed with the provider's API before money is credited."""
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    handled = await uc.online_payments.handle_webhook(
        provider=provider, tenant_id=school_id, headers=headers, body=body
    )
    return {"received": True, "matched": handled}
