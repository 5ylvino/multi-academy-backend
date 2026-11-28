"""The gate: who may collect each student, every release decision, and the visitor book."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.operations.gate import CollectorCommand, ReleaseCommand, VisitorCommand

router = APIRouter(prefix="/gate", tags=["gate"])

Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]
Phone = Annotated[str, Field(min_length=7, max_length=20, pattern=r"^\+?[0-9 ()-]+$")]


class CollectorRequest(ApiModel):
    name: str = Field(min_length=2, max_length=120)
    relationship: str = Field(min_length=1, max_length=40, description="e.g. aunt, driver")
    phone: Phone | None = None
    id_document: str | None = Field(
        default=None, max_length=80, description="e.g. 'Driver's licence ending 4471'"
    )
    valid_on: date | None = Field(
        default=None, description="One day only; leave out for every day until removed."
    )


class CollectorResponse(ApiModel):
    id: UUID
    name: str
    relationship: str
    phone: str | None
    id_document: str | None
    valid_on: date | None
    active: bool
    added_at: datetime


class GuardianOptionResponse(ApiModel):
    id: UUID
    name: str
    relationship: str


class PickupListResponse(ApiModel):
    student_id: UUID
    student_name: str
    guardians: list[GuardianOptionResponse]
    collectors: list[CollectorResponse] = Field(description="Only those allowed today.")


class ReleaseRequest(ApiModel):
    student_id: UUID
    guardian_id: UUID | None = None
    collector_id: UUID | None = None
    refused_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
        description="Someone not on the list tried to collect: the student stays.",
    )
    note: str | None = Field(default=None, max_length=300)


class ReleaseResponse(ApiModel):
    id: UUID
    student_id: UUID
    student_name: str
    status: str = Field(description="released or refused")
    guardian_id: UUID | None
    collector_id: UUID | None
    collector_name: str
    note: str | None
    by: UUID
    at: datetime


class ReleasePage(ApiModel):
    items: list[ReleaseResponse]
    page: PageMeta


class VisitorRequest(ApiModel):
    name: str = Field(min_length=2, max_length=120)
    purpose: str = Field(min_length=1, max_length=200)
    phone: Phone | None = None
    organisation: str | None = Field(default=None, max_length=120)
    host_id: UUID | None = Field(default=None, description="The member of staff they're seeing.")
    badge: str | None = Field(default=None, max_length=20)
    vehicle: str | None = Field(default=None, max_length=20)


class VisitResponse(ApiModel):
    id: UUID
    name: str
    phone: str | None
    organisation: str | None
    purpose: str
    host_id: UUID | None
    host_name: str | None
    badge: str | None
    vehicle: str | None
    signed_in_at: datetime
    signed_out_at: datetime | None


class VisitPage(ApiModel):
    items: list[VisitResponse]
    page: PageMeta


@router.get("/students/{student_id}/collectors", response_model=list[CollectorResponse])
async def collectors(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[CollectorResponse]:
    """Guardians and the office: everyone ever added, including removed and one-day entries."""
    found = await uc.gate.collectors(principal, student_id)
    return [CollectorResponse.model_validate(c) for c in found]


@router.post(
    "/students/{student_id}/collectors",
    response_model=CollectorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_collector(
    student_id: UUID, body: CollectorRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> CollectorResponse:
    """A guardian or the office adds an adult who may collect this student."""
    view = await uc.gate.add_collector(
        principal,
        student_id,
        CollectorCommand(
            name=body.name,
            relationship=body.relationship,
            phone=body.phone,
            id_document=body.id_document,
            valid_on=body.valid_on,
        ),
    )
    return CollectorResponse.model_validate(view)


@router.delete("/collectors/{collector_id}", response_model=CollectorResponse)
async def remove_collector(
    collector_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> CollectorResponse:
    """Takes effect at once. The entry is kept, marked inactive, for the record."""
    return CollectorResponse.model_validate(await uc.gate.remove_collector(principal, collector_id))


@router.get("/students/{student_id}/pickup", response_model=PickupListResponse)
async def pickup_list(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> PickupListResponse:
    """Gate staff: who may collect this student today."""
    return PickupListResponse.model_validate(await uc.gate.pickup_list(principal, student_id))


@router.post("/releases", response_model=ReleaseResponse, status_code=status.HTTP_201_CREATED)
async def release(
    body: ReleaseRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ReleaseResponse:
    """Give exactly one of guardian_id, collector_id or refused_name. Guardians are told."""
    view = await uc.gate.release(
        principal,
        ReleaseCommand(
            student_id=body.student_id,
            guardian_id=body.guardian_id,
            collector_id=body.collector_id,
            refused_name=body.refused_name,
            note=body.note,
        ),
    )
    return ReleaseResponse.model_validate(view)


@router.get("/releases", response_model=ReleasePage)
async def releases(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ReleasePage:
    """Newest first, the last 90 days. Guardians pass their child's id."""
    page = await uc.gate.releases(principal, student_id=student_id, cursor=cursor, limit=limit)
    return ReleasePage(
        items=[ReleaseResponse.model_validate(r) for r in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.post("/visits", response_model=VisitResponse, status_code=status.HTTP_201_CREATED)
async def sign_in(
    body: VisitorRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> VisitResponse:
    view = await uc.gate.sign_in(
        principal,
        VisitorCommand(
            name=body.name,
            purpose=body.purpose,
            phone=body.phone,
            organisation=body.organisation,
            host_id=body.host_id,
            badge=body.badge,
            vehicle=body.vehicle,
        ),
    )
    return VisitResponse.model_validate(view)


@router.post("/visits/{visit_id}/sign-out", response_model=VisitResponse)
async def sign_out(visit_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> VisitResponse:
    return VisitResponse.model_validate(await uc.gate.sign_out(principal, visit_id))


@router.get("/visits", response_model=VisitPage)
async def visits(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    on_site: bool = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> VisitPage:
    """on_site=true is the fire register: everyone signed in and not yet out."""
    page = await uc.gate.visits(principal, on_site_only=on_site, cursor=cursor, limit=limit)
    return VisitPage(
        items=[VisitResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )
