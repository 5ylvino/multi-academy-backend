"""School transport: routes and stops, students on each route, the driver's manifest."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.operations.transport import RouteCommand, StopCommand

router = APIRouter(prefix="/transport", tags=["transport"])

Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]
DirectionQ = Annotated[str, Query(pattern="^(to_school|from_school)$")]


class RouteRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9-]+$")
    vehicle_plate: str | None = Field(default=None, max_length=15)
    capacity: int = Field(ge=1, le=120, description="Seats for students.")
    driver_id: UUID | None = Field(default=None, description="A school user with the driver role.")


class RouteUpdateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    vehicle_plate: str | None = Field(default=None, max_length=15)
    capacity: int = Field(ge=1, le=120)
    driver_id: UUID | None = None
    active: bool = True


class StopRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    position: int = Field(ge=1, le=40, description="Order along the route from the first pickup.")
    morning_at: time | None = Field(default=None, description="Pickup time, school's local time.")
    afternoon_at: time | None = Field(default=None, description="Drop-off time.")
    landmark: str | None = Field(default=None, max_length=120)


class StopResponse(ApiModel):
    id: UUID
    name: str
    position: int
    morning_at: time | None
    afternoon_at: time | None
    landmark: str | None


class RouteResponse(ApiModel):
    id: UUID
    name: str
    code: str
    vehicle_plate: str | None
    capacity: int
    riders: int
    driver_id: UUID | None
    driver_name: str | None
    active: bool
    stops: list[StopResponse]


class AssignRequest(ApiModel):
    route_id: UUID
    stop_id: UUID


class TripStatusResponse(ApiModel):
    kind: str = Field(description="boarded, dropped or absent")
    at: datetime


class StudentTransportResponse(ApiModel):
    student_id: UUID
    student_name: str
    route_id: UUID
    route_name: str
    route_code: str
    vehicle_plate: str | None
    driver_name: str | None
    stop: StopResponse
    to_school: TripStatusResponse | None = Field(description="Today's latest, if any.")
    from_school: TripStatusResponse | None


class ManifestRowResponse(ApiModel):
    student_id: UUID
    student_name: str
    stop_id: UUID
    stop_name: str
    position: int
    status: str | None
    at: datetime | None


class ManifestResponse(ApiModel):
    route_id: UUID
    route_code: str
    route_name: str
    direction: str
    day: date
    riders: list[ManifestRowResponse]
    on_board: int


class BoardingRequest(ApiModel):
    student_id: UUID
    direction: str = Field(pattern="^(to_school|from_school)$")
    kind: str = Field(
        pattern="^(boarded|dropped|absent)$",
        description="absent: not at the stop in the morning, or not on the bus at home time.",
    )


class BoardingResponse(ApiModel):
    id: UUID
    student_id: UUID
    route_id: UUID
    stop_id: UUID | None
    direction: str
    kind: str
    day: date
    at: datetime


class BoardingPage(ApiModel):
    items: list[BoardingResponse]
    page: PageMeta


@router.get("/routes", response_model=list[RouteResponse])
async def routes(
    principal: SchoolPrincipal, uc: UseCasesDep, include_inactive: bool = False
) -> list[RouteResponse]:
    """Staff. A driver sees the routes they drive."""
    found = await uc.transport.routes(principal, include_inactive=include_inactive)
    return [RouteResponse.model_validate(r) for r in found]


@router.post("/routes", response_model=RouteResponse, status_code=status.HTTP_201_CREATED)
async def create_route(
    body: RouteRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RouteResponse:
    view = await uc.transport.create_route(
        principal,
        RouteCommand(
            name=body.name,
            code=body.code,
            vehicle_plate=body.vehicle_plate,
            capacity=body.capacity,
            driver_id=body.driver_id,
        ),
    )
    return RouteResponse.model_validate(view)


@router.get("/routes/{route_id}", response_model=RouteResponse)
async def route(route_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> RouteResponse:
    return RouteResponse.model_validate(await uc.transport.route(principal, route_id))


@router.put("/routes/{route_id}", response_model=RouteResponse)
async def update_route(
    route_id: UUID, body: RouteUpdateRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RouteResponse:
    """Set active to false to stop running the route (move its students first)."""
    view = await uc.transport.update_route(
        principal,
        route_id,
        RouteCommand(
            name=body.name,
            vehicle_plate=body.vehicle_plate,
            capacity=body.capacity,
            driver_id=body.driver_id,
            active=body.active,
        ),
    )
    return RouteResponse.model_validate(view)


@router.post(
    "/routes/{route_id}/stops", response_model=StopResponse, status_code=status.HTTP_201_CREATED
)
async def add_stop(
    route_id: UUID, body: StopRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> StopResponse:
    view = await uc.transport.add_stop(principal, route_id, _stop(body))
    return StopResponse.model_validate(view)


@router.put("/stops/{stop_id}", response_model=StopResponse)
async def update_stop(
    stop_id: UUID, body: StopRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> StopResponse:
    view = await uc.transport.update_stop(principal, stop_id, _stop(body))
    return StopResponse.model_validate(view)


@router.delete("/stops/{stop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_stop(stop_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> None:
    """Only when no student uses it. Past trips keep their stop."""
    await uc.transport.remove_stop(principal, stop_id)


@router.get("/routes/{route_id}/manifest", response_model=ManifestResponse)
async def manifest(
    route_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep, direction: DirectionQ
) -> ManifestResponse:
    """Today's riders in pickup order, each with their latest status on this trip."""
    view = await uc.transport.manifest(principal, route_id, direction=direction)
    return ManifestResponse.model_validate(view)


@router.post(
    "/routes/{route_id}/boardings",
    response_model=BoardingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record(
    route_id: UUID, body: BoardingRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> BoardingResponse:
    """The route's driver. Guardians are told at once."""
    view = await uc.transport.record(
        principal,
        route_id,
        student_id=body.student_id,
        direction=body.direction,
        kind=body.kind,
    )
    return BoardingResponse.model_validate(view)


@router.get("/students/{student_id}", response_model=StudentTransportResponse | None)
async def student_transport(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> StudentTransportResponse | None:
    """The student, their guardians, or staff. null when the student doesn't use the bus."""
    view = await uc.transport.student_transport(principal, student_id)
    return StudentTransportResponse.model_validate(view) if view else None


@router.put("/students/{student_id}", response_model=StudentTransportResponse)
async def assign(
    student_id: UUID, body: AssignRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> StudentTransportResponse:
    """Puts the student on a route and stop, or moves them. Seats are checked."""
    view = await uc.transport.assign(
        principal, student_id, route_id=body.route_id, stop_id=body.stop_id
    )
    return StudentTransportResponse.model_validate(view)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unassign(student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> None:
    await uc.transport.unassign(principal, student_id)


@router.get("/students/{student_id}/boardings", response_model=BoardingPage)
async def history(
    student_id: UUID,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> BoardingPage:
    """The last 90 days of trips, newest first."""
    page = await uc.transport.history(principal, student_id, cursor=cursor, limit=limit)
    return BoardingPage(
        items=[BoardingResponse.model_validate(b) for b in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


def _stop(body: StopRequest) -> StopCommand:
    return StopCommand(
        name=body.name,
        position=body.position,
        morning_at=body.morning_at,
        afternoon_at=body.afternoon_at,
        landmark=body.landmark,
    )
