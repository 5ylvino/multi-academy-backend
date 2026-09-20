from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CheckoutCreateRequest(BaseModel):
    context: str = Field(..., description="Payment button hook key, e.g. school_invoice")
    amountMinor: int = Field(..., gt=0)
    email: str
    callbackUrl: str
    currency: str | None = None
    reference: str | None = None
    metadata: dict[str, Any] | None = None
    gatewayId: str | None = Field(default=None, description="Optional gateway override for this checkout")
    createdBy: str | None = None


class CheckoutCreateResponse(BaseModel):
    sessionId: str
    reference: str
    contextKey: str
    gatewayId: str
    checkoutUrl: str
    accessCode: str | None = None
    status: str


class VerifyRequest(BaseModel):
    reference: str


class VerifyResponse(BaseModel):
    reference: str
    status: str
    gatewayId: str
    amountMinor: int
    currency: str
    contextKey: str
    sessionId: str
    providerReference: str = ""


class ContextItemResponse(BaseModel):
    contextKey: str
    label: str
    description: str
    enabled: bool
    buttonEnabled: bool
    featuresSatisfied: bool
    requiredFeatures: list[str]
    gatewayId: str | None
    allowedGateways: list[str]


class ContextListResponse(BaseModel):
    items: list[ContextItemResponse]


class ContextUpdateRequest(BaseModel):
    enabled: bool | None = None
    gatewayId: str | None = None
    clearGateway: bool = False


class GatewayItemResponse(BaseModel):
    gatewayId: str
    label: str
    enabled: bool


class GatewayListResponse(BaseModel):
    items: list[GatewayItemResponse]


class GatewayUpdateRequest(BaseModel):
    enabled: bool
