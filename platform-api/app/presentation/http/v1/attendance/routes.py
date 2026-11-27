"""Student registers and staff check-in."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.domain.attendance.staff import Site
from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.attendance.schemas import (
    CheckInRequest,
    CheckInResponse,
    CorrectionRequest,
    KioskCodeResponse,
    RegisterEntryResponse,
    RegisterResponse,
    SiteModel,
    StaffRegisterRowResponse,
    StaffSettingsModel,
    SubmitRegisterRequest,
    SubmitRegisterResponse,
    SummaryResponse,
)
from app.use_cases.attendance.registers import MarkInput
from app.use_cases.attendance.staff import SettingsView, UpdateSettingsCommand

router = APIRouter(prefix="/attendance", tags=["attendance"])

DayQuery = Annotated[date | None, Query(alias="date")]


def _settings(view: SettingsView) -> StaffSettingsModel:
    return StaffSettingsModel(
        window_opens=view.window_opens,
        late_after=view.late_after,
        window_closes=view.window_closes,
        business_days=view.business_days,
        check_in_roles=view.check_in_roles,
        sites=[
            SiteModel(name=s.name, latitude=s.latitude, longitude=s.longitude, radius_m=s.radius_m)
            for s in view.sites
        ],
        require_location=view.require_location,
    )


# ---- student registers -------------------------------------------------------------------


@router.get("/classes/{class_id}/register", response_model=RegisterResponse)
async def get_register(
    class_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep, day: DayQuery = None
) -> RegisterResponse:
    return RegisterResponse.model_validate(
        await uc.student_registers.register(principal, class_id, day)
    )


@router.post(
    "/classes/{class_id}/register",
    response_model=SubmitRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_register(
    class_id: UUID, body: SubmitRegisterRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SubmitRegisterResponse:
    result = await uc.student_registers.submit(
        principal,
        class_id,
        body.day,
        [MarkInput(student_id=e.student_id, status=e.status, note=e.note) for e in body.entries],
    )
    return SubmitRegisterResponse.model_validate(result)


@router.post("/classes/{class_id}/corrections", response_model=RegisterEntryResponse)
async def correct_mark(
    class_id: UUID, body: CorrectionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RegisterEntryResponse:
    entry = await uc.student_registers.correct(
        principal,
        class_id,
        day=body.day,
        student_id=body.student_id,
        status=body.status,
        reason=body.reason,
    )
    return RegisterEntryResponse.model_validate(entry)


@router.get("/students/{student_id}/summary", response_model=SummaryResponse)
async def student_summary(
    student_id: UUID,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> SummaryResponse:
    view = await uc.student_registers.summary(principal, student_id, start=start, end=end)
    return SummaryResponse.model_validate(view)


# ---- staff check-in ----------------------------------------------------------------------


@router.get("/staff/settings", response_model=StaffSettingsModel)
async def get_staff_settings(principal: SchoolPrincipal, uc: UseCasesDep) -> StaffSettingsModel:
    return _settings(await uc.staff_attendance.get_settings(principal))


@router.put("/staff/settings", response_model=StaffSettingsModel)
async def update_staff_settings(
    body: StaffSettingsModel, principal: SchoolPrincipal, uc: UseCasesDep
) -> StaffSettingsModel:
    view = await uc.staff_attendance.update_settings(
        principal,
        UpdateSettingsCommand(
            window_opens=body.window_opens,
            late_after=body.late_after,
            window_closes=body.window_closes,
            business_days=body.business_days,
            check_in_roles=body.check_in_roles,
            sites=[
                Site(name=s.name, latitude=s.latitude, longitude=s.longitude, radius_m=s.radius_m)
                for s in body.sites
            ],
            require_location=body.require_location,
        ),
    )
    return _settings(view)


@router.get("/staff/kiosk-code", response_model=KioskCodeResponse)
async def kiosk_code(principal: SchoolPrincipal, uc: UseCasesDep) -> KioskCodeResponse:
    return KioskCodeResponse.model_validate(await uc.staff_attendance.kiosk_code(principal))


@router.post("/staff/check-in", response_model=CheckInResponse, status_code=status.HTTP_201_CREATED)
async def check_in(
    body: CheckInRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> CheckInResponse:
    view = await uc.staff_attendance.check_in(
        principal, code=body.code, latitude=body.latitude, longitude=body.longitude
    )
    return CheckInResponse.model_validate(view)


@router.get("/staff/register", response_model=list[StaffRegisterRowResponse])
async def staff_register(
    principal: SchoolPrincipal, uc: UseCasesDep, day: DayQuery = None
) -> list[StaffRegisterRowResponse]:
    rows = await uc.staff_attendance.day_register(principal, day)
    return [StaffRegisterRowResponse.model_validate(r) for r in rows]


@router.get("/staff/me", response_model=list[CheckInResponse])
async def my_check_ins(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> list[CheckInResponse]:
    views = await uc.staff_attendance.my_history(principal, start=start, end=end)
    return [CheckInResponse.model_validate(v) for v in views]
