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


@router.get("/contexts", response_model=ContextListResponse)
def list_contexts(
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> ContextListResponse:
    items = [ContextItemResponse(**row) for row in checkout_service.list_contexts_for_tenant(db, ctx)]
    return ContextListResponse(items=items)


@router.patch("/contexts/{context_key}")
def update_context(
    context_key: str,
    body: ContextUpdateRequest,
    ctx: RequestContext = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return checkout_service.update_context_binding(
            db,
            ctx.tenant_id,
            context_key,
            enabled=body.enabled,
            gateway_id=body.gatewayId,
            clear_gateway=body.clearGateway,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gateways", response_model=GatewayListResponse)
def list_gateways(
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> GatewayListResponse:
    items = [
        GatewayItemResponse(**row) for row in checkout_service.list_gateways_for_tenant(db, ctx.tenant_id)
    ]
    return GatewayListResponse(items=items)


@router.patch("/gateways/{gateway_id}")
def update_gateway(
    gateway_id: str,
    body: GatewayUpdateRequest,
    ctx: RequestContext = Depends(require_admin()),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return checkout_service.update_gateway_override(
            db, ctx.tenant_id, gateway_id, enabled=body.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
