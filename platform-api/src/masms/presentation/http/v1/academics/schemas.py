"""Academic structure models: sessions, terms, classes, subjects, enrolments."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from masms.presentation.http.v1.schemas import ApiModel

SchoolLevelName = Literal["nursery", "primary", "secondary"]
TermCodeName = Literal["first", "second", "third"]


class CreateSessionRequest(ApiModel):
    name: str = Field(min_length=4, max_length=64, examples=["2026/2027"])
    starts_on: date
    ends_on: date


class CreateTermRequest(ApiModel):
    code: TermCodeName
    name: str | None = Field(default=None, max_length=64)
    starts_on: date
    ends_on: date


class TermResponse(ApiModel):
    id: UUID
    session_id: UUID
    code: str
    name: str
    starts_on: date
    ends_on: date
    is_current: bool


class SessionResponse(ApiModel):
    id: UUID
    name: str
    starts_on: date
    ends_on: date
    is_current: bool
    terms: list[TermResponse]


class CreateClassRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=32)
    school_level: SchoolLevelName
    capacity: int | None = Field(default=None, ge=1, le=1000)
    class_teacher_id: UUID | None = None


class UpdateClassRequest(ApiModel):
    """Partial update. Send ``class_teacher_id: null`` to remove the class teacher."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    capacity: int | None = Field(default=None, ge=1, le=1000)
    class_teacher_id: UUID | None = None
    active: bool | None = None


class ClassResponse(ApiModel):
    id: UUID
    name: str
    code: str
    school_level: str
    class_teacher_id: UUID | None
    capacity: int | None
    active: bool
    subject_ids: list[UUID]


class CreateSubjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=32)
    school_level: SchoolLevelName
    description: str = Field(default="", max_length=2000)


class SubjectResponse(ApiModel):
    id: UUID
    name: str
    code: str
    school_level: str
    description: str
    active: bool


class LinkSubjectRequest(ApiModel):
    teacher_id: UUID | None = None


class EnrolRequest(ApiModel):
    student_id: UUID
    class_id: UUID


class EnrolmentResponse(ApiModel):
    id: UUID
    student_id: UUID
    class_id: UUID
    session_id: UUID
    status: str
    enrolled_at: datetime
