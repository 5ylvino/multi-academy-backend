from fastapi import APIRouter, Depends, HTTPException

from app.schemas.tutoring import BookingCreateRequest, PaymentIntentRequest
from app.security.context import RequestContext
from app.security.dependencies import require_feature
from app.services.ai_bridge import AiBridgeService
from app.services.booking_service import BookingService

router = APIRouter(prefix="/v1", tags=["bookings"])


@router.get("/bookings/mine")
async def list_my_bookings(
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    result = BookingService().list_mine(ctx)
    return result.model_dump(by_alias=True)


@router.post("/bookings")
async def create_booking(
    payload: BookingCreateRequest,
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    try:
        result = BookingService().create(ctx, payload)
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/bookings/{booking_id}/confirm")
async def confirm_booking(
    booking_id: str,
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    try:
        result = BookingService().confirm(ctx, booking_id)
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/payments/intent")
async def payment_intent(
    payload: PaymentIntentRequest,
    ctx: RequestContext = Depends(require_feature("tutoring.payments")),
) -> dict:
    try:
        result = await BookingService().create_payment_intent(ctx, payload.booking_id)
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/payments/{payment_id}/settle")
async def settle_payment(
    payment_id: str,
    payload: dict,
    ctx: RequestContext = Depends(require_feature("tutoring.payments")),
) -> dict:
    try:
        result = BookingService().settle_payment(
            ctx,
            payment_id,
            provider_reference=payload.get("providerReference") or payload.get("provider_reference"),
            checkout_reference=payload.get("checkoutReference") or payload.get("checkout_reference"),
        )
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{booking_id}/ai-prep")
async def ai_prep(
    booking_id: str,
    ctx: RequestContext = Depends(require_feature("tutoring.ai_hybrid")),
) -> dict:
    try:
        result = await AiBridgeService().start_ai_prep(ctx, booking_id)
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
