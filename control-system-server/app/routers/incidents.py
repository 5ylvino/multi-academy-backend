from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import Incident, StatusComponent
from app.models.incidents import COMPONENT_STATUSES, INCIDENT_STATUSES
from app.schemas import (
    IncidentCreate,
    IncidentOut,
    IncidentUpdate,
    StatusComponentOut,
    StatusComponentUpdate,
)

router = APIRouter(prefix="/v1", tags=["incidents"])
public_router = APIRouter(prefix="/public/v1", tags=["public-status"])
settings = get_settings()


# ---------------------------------------------------------------- staff

@router.get("/status/components", response_model=list[StatusComponentOut])
def list_components(
    _: StaffContext = Depends(require_permissions("incidents:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(StatusComponent).order_by(StatusComponent.sort_order, StatusComponent.key)
    ).scalars().all()


@router.patch("/status/components/{key}", response_model=StatusComponentOut)
def update_component(
    key: str,
    body: StatusComponentUpdate,
    staff: StaffContext = Depends(require_permissions("incidents:write")),
    db: Session = Depends(get_db),
):
    comp = db.execute(
        select(StatusComponent).where(StatusComponent.key == key)
    ).scalar_one_or_none()
    if comp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Component not found")
    before = {"status": comp.status}
    if body.status is not None:
        if body.status not in COMPONENT_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {COMPONENT_STATUSES}")
        comp.status = body.status
    if body.description is not None:
        comp.description = body.description
    if body.name is not None:
        comp.name = body.name
    if body.is_public is not None:
        comp.is_public = body.is_public
    record_audit(
        db,
        action="status.component.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="status_component",
        target_id=key,
        before=before,
        after={"status": comp.status},
    )
    db.commit()
    db.refresh(comp)
    return comp


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    _: StaffContext = Depends(require_permissions("incidents:read")),
    db: Session = Depends(get_db),
):
    return db.execute(select(Incident).order_by(Incident.started_at.desc()).limit(100)).scalars().all()


@router.post("/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(
    body: IncidentCreate,
    staff: StaffContext = Depends(require_permissions("incidents:write")),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    updates = []
    if body.initial_update:
        updates.append(
            {
                "at": now.isoformat(),
                "status": "investigating",
                "message": body.initial_update,
                "by": staff.email,
            }
        )
    incident = Incident(
        title=body.title,
        impact=body.impact,
        summary=body.summary,
        affected_components=body.affected_components,
        is_public=body.is_public,
        updates=updates,
        created_by=staff.email,
        status="investigating",
    )
    db.add(incident)
    # Degrade affected public components
    for key in body.affected_components:
        comp = db.execute(
            select(StatusComponent).where(StatusComponent.key == key)
        ).scalar_one_or_none()
        if comp and comp.status == "operational":
            comp.status = "degraded" if body.impact != "critical" else "outage"
    record_audit(
        db,
        action="incident.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="incident",
        after={"title": body.title, "impact": body.impact},
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.patch("/incidents/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: int,
    body: IncidentUpdate,
    staff: StaffContext = Depends(require_permissions("incidents:write")),
    db: Session = Depends(get_db),
):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    before = {"status": incident.status}
    if body.status is not None:
        if body.status not in INCIDENT_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {INCIDENT_STATUSES}")
        incident.status = body.status
        if body.status == "resolved":
            incident.resolved_at = datetime.now(timezone.utc)
            for key in incident.affected_components or []:
                comp = db.execute(
                    select(StatusComponent).where(StatusComponent.key == key)
                ).scalar_one_or_none()
                if comp:
                    comp.status = "operational"
    if body.impact is not None:
        incident.impact = body.impact
    if body.summary is not None:
        incident.summary = body.summary
    if body.affected_components is not None:
        incident.affected_components = body.affected_components
    if body.is_public is not None:
        incident.is_public = body.is_public
    if body.postmortem_url is not None:
        incident.postmortem_url = body.postmortem_url
    if body.update_message:
        updates = list(incident.updates or [])
        updates.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "status": incident.status,
                "message": body.update_message,
                "by": staff.email,
            }
        )
        incident.updates = updates
    record_audit(
        db,
        action="incident.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="incident",
        target_id=str(incident_id),
        before=before,
        after={"status": incident.status},
    )
    db.commit()
    db.refresh(incident)
    return incident


# ---------------------------------------------------------------- public

@public_router.get("/status")
def public_status(db: Session = Depends(get_db)):
    components = db.execute(
        select(StatusComponent)
        .where(StatusComponent.is_public.is_(True))
        .order_by(StatusComponent.sort_order)
    ).scalars().all()
    open_incidents = db.execute(
        select(Incident)
        .where(Incident.is_public.is_(True), Incident.status != "resolved")
        .order_by(Incident.started_at.desc())
        .limit(20)
    ).scalars().all()
    recent_resolved = db.execute(
        select(Incident)
        .where(Incident.is_public.is_(True), Incident.status == "resolved")
        .order_by(Incident.resolved_at.desc())
        .limit(10)
    ).scalars().all()

    overall = "operational"
    for c in components:
        if c.status == "outage":
            overall = "outage"
            break
        if c.status in ("degraded", "maintenance") and overall == "operational":
            overall = c.status

    return {
        "title": settings.status_page_title,
        "url": settings.status_page_url,
        "overall": overall,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "components": [
            {
                "key": c.key,
                "name": c.name,
                "description": c.description,
                "status": c.status,
            }
            for c in components
        ],
        "activeIncidents": [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "impact": i.impact,
                "summary": i.summary,
                "updates": i.updates,
                "startedAt": i.started_at.isoformat() if i.started_at else None,
            }
            for i in open_incidents
        ],
        "recentResolved": [
            {
                "id": i.id,
                "title": i.title,
                "resolvedAt": i.resolved_at.isoformat() if i.resolved_at else None,
                "postmortemUrl": i.postmortem_url or None,
            }
            for i in recent_resolved
        ],
    }
