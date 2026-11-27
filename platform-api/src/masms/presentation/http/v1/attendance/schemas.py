"""Attendance request and response models."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import Field

from masms.presentation.http.v1.schemas import ApiModel

StatusName = Literal["present", "late", "absent", "excused"]


class RegisterEntryResponse(ApiModel):
    student_id: UUID
    full_name: str
    status: str | None
    marked_at: datetime | None
    corrected: bool
    note: str | None


class RegisterResponse(ApiModel):
    class_id: UUID
    day: date
    session_id: UUID
    complete: bool
    entries: list[RegisterEntryResponse]


class MarkRequest(ApiModel):
    student_id: UUID
    status: StatusName
    note: str | None = Field(default=None, max_length=300)


class SubmitRegisterRequest(ApiModel):
    day: date | None = Field(default=None, description="Defaults to today, school time")
    entries: list[MarkRequest] = Field(min_length=1, max_length=1000)


class SubmitRegisterResponse(ApiModel):
    recorded: int
    already_marked: list[UUID]


class CorrectionRequest(ApiModel):
    day: date
    student_id: UUID
    status: StatusName
    reason: str = Field(min_length=3, max_length=500)


class SummaryResponse(ApiModel):
    student_id: UUID
    start: date
    end: date
    present: int
    late: int
    absent: int
    excused: int
    attendance_rate: float | None


class SiteModel(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=25, le=2000)


class StaffSettingsModel(ApiModel):
    window_opens: time
    late_after: time
    window_closes: time
    business_days: list[int] = Field(min_length=1, max_length=7)
    check_in_roles: list[str] = Field(max_length=20)
    sites: list[SiteModel] = Field(default_factory=list, max_length=20)
    require_location: bool = False


class KioskCodeResponse(ApiModel):
    code: str
    expires_in: int


class CheckInRequest(ApiModel):
    code: str = Field(pattern=r"^\d{6}$")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class CheckInResponse(ApiModel):
    day: date
    status: str
    checked_in_at: datetime | None
    site_name: str | None


class StaffRegisterRowResponse(ApiModel):
    staff_id: UUID
    full_name: str
    roles: list[str]
    status: str
    checked_in_at: datetime | None
    site_name: str | None
