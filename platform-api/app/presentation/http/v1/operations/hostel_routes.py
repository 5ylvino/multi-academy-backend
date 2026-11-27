"""Boarding: hostels and rooms, boarders' rooms and beds, and exeats."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import AwareDatetime, Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.operations.hostel import ExeatCommand

router = APIRouter(prefix="/hostels", tags=["hostels"])

Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]
STATUSES = "pending|approved|rejected|cancelled|out|returned"


class HostelRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(boys|girls|mixed)$")
    warden_id: UUID | None = Field(default=None, description="The house master or mistress.")


class HostelUpdateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    warden_id: UUID | None = None
    active: bool = True


class RoomRequest(ApiModel):
    name: str = Field(min_length=1, max_length=40)
    capacity: int = Field(ge=1, le=40, description="Beds.")


class RoomUpdateRequest(RoomRequest):
    active: bool = True


class RoomResponse(ApiModel):
    id: UUID
    name: str
    capacity: int
    occupants: int


class HostelResponse(ApiModel):
    id: UUID
    name: str
    kind: str
    warden_id: UUID | None
    warden_name: str | None
    active: bool
    beds: int
    boarders: int
    rooms: list[RoomResponse]


class ResidentResponse(ApiModel):
    student_id: UUID
    student_name: str
    room_id: UUID
    room_name: str
    bed: str | None
    away: bool = Field(description="Out on an exeat right now.")


class AllocateRequest(ApiModel):
    room_id: UUID
    bed: str | None = Field(default=None, max_length=10, description="e.g. 3A")


class ExeatRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    collector_name: str = Field(min_length=2, max_length=120, description="Who collects them.")
    leave_at: AwareDatetime
    return_by: AwareDatetime


class DecisionRequest(ApiModel):
    note: str | None = Field(
        default=None, max_length=500, description="Required when turning it down."
    )


class ExeatResponse(ApiModel):
    id: UUID
    student_id: UUID
    student_name: str
    reason: str
    collector_name: str
    leave_at: datetime
    return_by: datetime
    status: str
    requested_by: UUID
    requested_by_name: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    left_at: datetime | None
    returned_at: datetime | None
    overdue: bool
    returned_late: bool


class ExeatPage(ApiModel):
    items: list[ExeatResponse]
    page: PageMeta


class BoarderResponse(ApiModel):
    student_id: UUID
    student_name: str
    hostel_id: UUID
    hostel_name: str
    room_id: UUID
    room_name: str
    bed: str | None
    warden_name: str | None
    exeat: ExeatResponse | None = Field(description="The open exeat, if any.")


@router.get("", response_model=list[HostelResponse])
async def hostels(
    principal: SchoolPrincipal, uc: UseCasesDep, include_inactive: bool = False
) -> list[HostelResponse]:
    found = await uc.hostels.hostels(principal, include_inactive=include_inactive)
    return [HostelResponse.model_validate(h) for h in found]


@router.post("", response_model=HostelResponse, status_code=status.HTTP_201_CREATED)
async def create_hostel(
    body: HostelRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> HostelResponse:
    view = await uc.hostels.create_hostel(
        principal, name=body.name, kind=body.kind, warden_id=body.warden_id
    )
    return HostelResponse.model_validate(view)


@router.put("/{hostel_id}", response_model=HostelResponse)
async def update_hostel(
    hostel_id: UUID, body: HostelUpdateRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> HostelResponse:
    view = await uc.hostels.update_hostel(
        principal, hostel_id, name=body.name, warden_id=body.warden_id, active=body.active
    )
    return HostelResponse.model_validate(view)


@router.post("/{hostel_id}/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def add_room(
    hostel_id: UUID, body: RoomRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RoomResponse:
    view = await uc.hostels.add_room(principal, hostel_id, name=body.name, capacity=body.capacity)
    return RoomResponse.model_validate(view)


@router.put("/rooms/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: UUID, body: RoomUpdateRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RoomResponse:
    view = await uc.hostels.update_room(
        principal, room_id, name=body.name, capacity=body.capacity, active=body.active
    )
    return RoomResponse.model_validate(view)


@router.get("/{hostel_id}/residents", response_model=list[ResidentResponse])
async def residents(
    hostel_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[ResidentResponse]:
    """Boarders by room and bed, with who is away: the roll call."""
    found = await uc.hostels.residents(principal, hostel_id)
    return [ResidentResponse.model_validate(r) for r in found]


@router.get("/boarders/{student_id}", response_model=BoarderResponse | None)
async def boarder(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> BoarderResponse | None:
    """The boarder, their guardians or boarding staff. null for a day student."""
    view = await uc.hostels.boarder(principal, student_id)
    return BoarderResponse.model_validate(view) if view else None


@router.put("/boarders/{student_id}", response_model=BoarderResponse)
async def allocate(
    student_id: UUID, body: AllocateRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> BoarderResponse:
    """Gives the student a room (and bed), or moves them. Beds are checked."""
    view = await uc.hostels.allocate(principal, student_id, room_id=body.room_id, bed=body.bed)
    return BoarderResponse.model_validate(view)


@router.delete("/boarders/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def vacate(student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> None:
    await uc.hostels.vacate(principal, student_id)


@router.post(
    "/boarders/{student_id}/exeats",
    response_model=ExeatResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_exeat(
    student_id: UUID, body: ExeatRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExeatResponse:
    """A guardian, or boarding staff. Someone else approves it."""
    view = await uc.hostels.request_exeat(
        principal,
        student_id,
        ExeatCommand(
            reason=body.reason,
            collector_name=body.collector_name,
            leave_at=body.leave_at,
            return_by=body.return_by,
        ),
    )
    return ExeatResponse.model_validate(view)


@router.get("/exeats", response_model=ExeatPage)
async def exeats(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", pattern=f"^({STATUSES})$")] = None,
    overdue: bool = False,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ExeatPage:
    """overdue=true: boarders still out after their return time."""
    page = await uc.hostels.exeats(
        principal,
        student_id=student_id,
        status=status_filter,
        overdue=overdue,
        cursor=cursor,
        limit=limit,
    )
    return ExeatPage(
        items=[ExeatResponse.model_validate(e) for e in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.post("/exeats/{exeat_id}/approve", response_model=ExeatResponse)
async def approve(
    exeat_id: UUID, body: DecisionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExeatResponse:
    view = await uc.hostels.decide(principal, exeat_id, approve=True, note=body.note)
    return ExeatResponse.model_validate(view)


@router.post("/exeats/{exeat_id}/reject", response_model=ExeatResponse)
async def reject(
    exeat_id: UUID, body: DecisionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ExeatResponse:
    view = await uc.hostels.decide(principal, exeat_id, approve=False, note=body.note)
    return ExeatResponse.model_validate(view)


@router.post("/exeats/{exeat_id}/cancel", response_model=ExeatResponse)
async def cancel(exeat_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> ExeatResponse:
    return ExeatResponse.model_validate(await uc.hostels.cancel(principal, exeat_id))


@router.post("/exeats/{exeat_id}/sign-out", response_model=ExeatResponse)
async def sign_out(exeat_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> ExeatResponse:
    return ExeatResponse.model_validate(await uc.hostels.sign_out(principal, exeat_id))


@router.post("/exeats/{exeat_id}/sign-in", response_model=ExeatResponse)
async def sign_in(exeat_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> ExeatResponse:
    return ExeatResponse.model_validate(await uc.hostels.sign_in(principal, exeat_id))
