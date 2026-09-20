from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.payments import CheckoutCreateRequest, CheckoutCreateResponse, VerifyRequest, VerifyResponse
from app.security.context import RequestContext
from app.security.dependencies import get_request_context
from app.services.checkout_service import checkout_service
from app.services.gateway_resolver import GatewayResolutionError

router = APIRouter(prefix="/v1", tags=["checkout"])


@router.post("/checkout", response_model=CheckoutCreateResponse)
async def create_checkout(
    body: CheckoutCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> CheckoutCreateResponse:
    try:
        result = await checkout_service.create_checkout(
            db,
            ctx,
            context_key=body.context,
            amount_minor=body.amountMinor,
            email=body.email,
            callback_url=body.callbackUrl,
            currency=body.currency,
            reference=body.reference,
            metadata=body.metadata,
            gateway_id=body.gatewayId,
            created_by=body.createdBy,
        )
        return CheckoutCreateResponse(**result)
    except GatewayResolutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/verify", response_model=VerifyResponse)
async def verify_checkout(
    body: VerifyRequest,
    ctx: RequestContext = Depends(get_request_context),
    db: Session = Depends(get_db),
) -> VerifyResponse:
    try:
        result = await checkout_service.verify(db, ctx, reference=body.reference)
        return VerifyResponse(**result)
    except GatewayResolutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
