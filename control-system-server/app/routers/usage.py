"""Usage meters + FinOps summary (Phase B)."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import UsageMeter
from app.schemas import UsageIngest, UsageMeterOut
from app.services.usage_metering import (
    ensure_default_meters,
    ingest_usage_event,
    usage_alerts_summary,
    usage_csv_rows,
    usage_summary,
)

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get("/meters", response_model=list[UsageMeterOut])
def list_meters(
    _: StaffContext = Depends(require_permissions("usage:read")),
    db: Session = Depends(get_db),
):
    ensure_default_meters(db)
    db.commit()
    return db.execute(select(UsageMeter).order_by(UsageMeter.key)).scalars().all()


@router.get("/summary")
def get_usage_summary(
    period: str | None = Query(default=None, description="YYYY-MM"),
    _: StaffContext = Depends(require_permissions("usage:read")),
    db: Session = Depends(get_db),
):
    ensure_default_meters(db)
    db.commit()
    return {
        "period": period,
        "rows": usage_summary(db, period=period),
        "alerts": usage_alerts_summary(db, period=period),
    }


@router.get("/export.csv")
def export_usage_csv(
    period: str | None = Query(default=None, description="YYYY-MM"),
    _: StaffContext = Depends(require_permissions("usage:read")),
    db: Session = Depends(get_db),
):
    ensure_default_meters(db)
    db.commit()
    body = "\n".join(usage_csv_rows(db, period=period)) + "\n"
    filename = f"usage-{period or 'current'}.csv"
    return PlainTextResponse(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/alerts")
def list_usage_alerts(
    period: str | None = Query(default=None, description="YYYY-MM"),
    _: StaffContext = Depends(require_permissions("usage:read")),
    db: Session = Depends(get_db),
):
    return {"period": period, "alerts": usage_alerts_summary(db, period=period)}


@router.post("/events")
def staff_ingest_usage(
    body: UsageIngest,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    ensure_default_meters(db)
    event = ingest_usage_event(
        db,
        tenant_ref=body.tenant_ref,
        meter_key=body.meter_key,
        quantity=body.quantity,
        event_id=body.event_id,
        properties=body.properties,
    )
    record_audit(
        db,
        action="usage.ingest.staff",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="usage",
        target_id=str(event.id),
        after=body.model_dump(),
    )
    db.commit()
    return {"accepted": True, "eventId": event.id}
