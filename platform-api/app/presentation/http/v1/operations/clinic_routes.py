"""The school clinic: health records, the staff medical alert, and visits to the nurse."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.operations.clinic import RecordCommand, VisitNotes

router = APIRouter(prefix="/clinic", tags=["clinic"])

Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]
OUTCOMES = "returned_to_class|rested_in_clinic|sent_home|collected_by_parent|referred_to_hospital"
_PRIVATE = {"Cache-Control": "no-store"}
Item = Annotated[str, Field(min_length=1, max_length=120)]


class RecordRequest(ApiModel):
    blood_group: str | None = Field(default=None, pattern="^(A|B|AB|O)[+-]$")
    genotype: str | None = Field(default=None, pattern="^(AA|AS|AC|SS|SC|CC)$")
    allergies: list[Item] = Field(default_factory=list, max_length=30)
    conditions: list[Item] = Field(default_factory=list, max_length=30)
    medications: list[Item] = Field(default_factory=list, max_length=30)
    notes: str | None = Field(default=None, max_length=4_000)
    staff_alert: str | None = Field(
        default=None,
        max_length=300,
        description="What teachers should know, e.g. 'Asthma: inhaler in school bag'.",
    )
    doctor: str | None = Field(default=None, max_length=200, description="Family doctor/clinic.")


class RecordResponse(ApiModel):
    student_id: UUID
    blood_group: str | None
    genotype: str | None
    allergies: list[str]
    conditions: list[str]
    medications: list[str]
    notes: str | None
    staff_alert: str | None
    doctor: str | None
    updated_by: UUID | None
    updated_at: datetime | None


class AlertResponse(ApiModel):
    student_id: UUID
    student_name: str
    allergies: list[str]
    staff_alert: str | None


class OpenVisitRequest(ApiModel):
    student_id: UUID
    complaint: str = Field(min_length=1, max_length=1_000)
    arrived_at: datetime | None = Field(default=None, description="Defaults to now.")


class VisitNotesRequest(ApiModel):
    assessment: str | None = Field(default=None, max_length=4_000)
    treatment: str | None = Field(default=None, max_length=4_000)
    temperature_dc: int | None = Field(
        default=None, ge=300, le=450, description="Tenths of a degree Celsius: 372 is 37.2 C."
    )
    medication_given: list[Item] = Field(default_factory=list, max_length=30)


class CloseVisitRequest(ApiModel):
    outcome: str = Field(
        pattern=f"^({OUTCOMES})$",
        description="sent_home and referred_to_hospital also email the guardians.",
    )


class VisitResponse(ApiModel):
    id: UUID
    student_id: UUID
    student_name: str
    arrived_at: datetime
    complaint: str
    assessment: str | None
    treatment: str | None
    temperature_dc: int | None
    medication_given: list[str]
    outcome: str | None
    left_at: datetime | None
    recorded_by: UUID


class VisitPage(ApiModel):
    items: list[VisitResponse]
    page: PageMeta


@router.get("/students/{student_id}/record", response_model=RecordResponse)
async def record(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep, response: Response
) -> RecordResponse:
    """The nurse, the student's guardians or the student. Staff views are in the activity log."""
    response.headers.update(_PRIVATE)
    return RecordResponse.model_validate(await uc.clinic.record(principal, student_id))


@router.put("/students/{student_id}/record", response_model=RecordResponse)
async def save_record(
    student_id: UUID,
    body: RecordRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> RecordResponse:
    """The nurse or a guardian. Replaces the whole record."""
    response.headers.update(_PRIVATE)
    view = await uc.clinic.save_record(
        principal,
        student_id,
        RecordCommand(
            blood_group=body.blood_group,
            genotype=body.genotype,
            allergies=tuple(body.allergies),
            conditions=tuple(body.conditions),
            medications=tuple(body.medications),
            notes=body.notes,
            staff_alert=body.staff_alert,
            doctor=body.doctor,
        ),
    )
    return RecordResponse.model_validate(view)


@router.get("/students/{student_id}/alert", response_model=AlertResponse)
async def alert(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep, response: Response
) -> AlertResponse:
    """Staff: the allergies and the note the school shares with teachers. Nothing else."""
    response.headers.update(_PRIVATE)
    return AlertResponse.model_validate(await uc.clinic.alert(principal, student_id))


@router.post("/visits", response_model=VisitResponse, status_code=status.HTTP_201_CREATED)
async def open_visit(
    body: OpenVisitRequest, principal: SchoolPrincipal, uc: UseCasesDep, response: Response
) -> VisitResponse:
    response.headers.update(_PRIVATE)
    view = await uc.clinic.open_visit(
        principal, student_id=body.student_id, complaint=body.complaint, arrived_at=body.arrived_at
    )
    return VisitResponse.model_validate(view)


@router.get("/visits", response_model=VisitPage)
async def visits(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
    student_id: UUID | None = None,
    open_only: bool = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> VisitPage:
    """The nurse sees every visit of the last 60 days; guardians pass their child's id."""
    response.headers.update(_PRIVATE)
    page = await uc.clinic.visits(
        principal, student_id=student_id, open_only=open_only, cursor=cursor, limit=limit
    )
    return VisitPage(
        items=[VisitResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.patch("/visits/{visit_id}", response_model=VisitResponse)
async def update_visit(
    visit_id: UUID,
    body: VisitNotesRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> VisitResponse:
    response.headers.update(_PRIVATE)
    view = await uc.clinic.update_visit(
        principal,
        visit_id,
        VisitNotes(
            assessment=body.assessment,
            treatment=body.treatment,
            temperature_dc=body.temperature_dc,
            medication_given=tuple(body.medication_given),
        ),
    )
    return VisitResponse.model_validate(view)


@router.post("/visits/{visit_id}/close", response_model=VisitResponse)
async def close_visit(
    visit_id: UUID,
    body: CloseVisitRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> VisitResponse:
    """Records how the visit ended and tells the guardians."""
    response.headers.update(_PRIVATE)
    view = await uc.clinic.close_visit(principal, visit_id, outcome=body.outcome)
    return VisitResponse.model_validate(view)
