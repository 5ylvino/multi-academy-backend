from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.payments import (
    ContextItemResponse,
    ContextListResponse,
    ContextUpdateRequest,
    GatewayItemResponse,
    GatewayListResponse,
    GatewayUpdateRequest,
)
from app.security.context import RequestContext
from app.security.dependencies import get_request_context, require_admin
from app.services.checkout_service import checkout_service

router = APIRouter(prefix="/v1", tags=["contexts"])

_CONTROL_ONLY_DETAIL = (
    "Payment settings are managed in the control platform console. "
    "Contact your platform administrator to change payment buttons or gateways."
)


@router.get("/contexts", response_model=ContextListResponse)
async def list_contexts(
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> ContextListResponse:
    items = [
        ContextItemResponse(**row)
        for row in await checkout_service.list_contexts_for_tenant(db, ctx)
    ]
    return ContextListResponse(items=items)


@router.patch("/contexts/{context_key}")
def update_context(
    context_key: str,
    body: ContextUpdateRequest,
    ctx: RequestContext = Depends(require_admin()),
) -> dict:
    del context_key, body, ctx
    raise HTTPException(status_code=403, detail=_CONTROL_ONLY_DETAIL)


@router.get("/gateways", response_model=GatewayListResponse)
async def list_gateways(
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> GatewayListResponse:
    items = [
        GatewayItemResponse(**row)
        for row in await checkout_service.list_gateways_for_tenant(db, ctx.tenant_id)
    ]
    return GatewayListResponse(items=items)


@router.patch("/gateways/{gateway_id}")
def update_gateway(
    gateway_id: str,
    body: GatewayUpdateRequest,
    ctx: RequestContext = Depends(require_admin()),
) -> dict:
    del gateway_id, body, ctx
    raise HTTPException(status_code=403, detail=_CONTROL_ONLY_DETAIL)
