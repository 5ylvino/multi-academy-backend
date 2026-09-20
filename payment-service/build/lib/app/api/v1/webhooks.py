from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.webhook_service import webhook_service

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/{gateway_id}")
async def payment_webhook(
    gateway_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    raw_body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    try:
        return await webhook_service.handle(
            db,
            gateway_id=gateway_id,
            headers=headers,
            raw_body=raw_body,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
