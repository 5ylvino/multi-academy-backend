"""The academic calendar and the school's class and subject structure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from masms.domain.shared.entity import AggregateRoot, Entity
from masms.domain.shared.errors import ValidationFailed
from masms.domain.shared.ids import new_id

_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


def normalise_code(code: str, field_name: str = "code") -> str:
    value = code.strip().upper().replace(" ", "-")
    if not _CODE.match(value):
        raise ValidationFailed(
            "Codes are 1-32 letters, digits, hyphens or underscores.", details={"field": field_name}
        )
    return value


def _name(value: str, field_name: str = "name", max_len: int = 120) -> str:
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= max_len:
        raise ValidationFailed(
            f"Enter a name of up to {max_len} characters.", details={"field": field_name}
        )
    return clean


class SchoolLevelCode(StrEnum):
    NURSERY = "nursery"
    PRIMARY = "primary"
    SECONDARY = "secondary"


class TermCode(StrEnum):
    FIRST = "first"
    SECOND = "second"
    THIRD = "third"


@dataclass(kw_only=True, eq=False)
class AcademicSession(AggregateRoot):
    tenant_id: UUID
    name: str
    starts_on: date
    ends_on: date
    is_current: bool = False
    created_at: datetime

    @classmethod
    def create(
        cls, *, tenant_id: UUID, name: str, starts_on: date, ends_on: date, now: datetime
    ) -> AcademicSession:
        if ends_on <= starts_on:
            raise ValidationFailed(
                "The session must end after it starts.", details={"field": "ends_on"}
            )
        if (ends_on - starts_on).days > 450:
            raise ValidationFailed(
                "A session can't be longer than 15 months.", details={"field": "ends_on"}
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name, max_len=64),
            starts_on=starts_on,
            ends_on=ends_on,
            created_at=now,
        )


@dataclass(kw_only=True, eq=False)
class Term(Entity):
    tenant_id: UUID
    session_id: UUID
    code: TermCode
    name: str
    starts_on: date
    ends_on: date
    is_current: bool = False

    @classmethod
    def create(
        cls,
        *,
        session: AcademicSession,
        code: TermCode,
        name: str | None,
        starts_on: date,
        ends_on: date,
    ) -> Term:
        if ends_on <= starts_on:
            raise ValidationFailed(
                "The term must end after it starts.", details={"field": "ends_on"}
            )
        if starts_on < session.starts_on or ends_on > session.ends_on:
            raise ValidationFailed(
                "The term must fall inside its session's dates.", details={"field": "starts_on"}
            )
        return cls(
            id=new_id(),
            tenant_id=session.tenant_id,
            session_id=session.id,
            code=code,
            name=_name(name or f"{code.value.title()} Term", max_len=64),
            starts_on=starts_on,
            ends_on=ends_on,
        )


@dataclass(kw_only=True, eq=False)
class SchoolClass(Entity):
    tenant_id: UUID
    name: str
    code: str
    school_level: SchoolLevelCode
    class_teacher_id: UUID | None = None
    capacity: int | None = None
    active: bool = True
    subject_ids: set[UUID] = field(default_factory=set)

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        code: str,
        school_level: SchoolLevelCode,
        capacity: int | None,
        class_teacher_id: UUID | None = None,
    ) -> SchoolClass:
        if capacity is not None and not 1 <= capacity <= 1000:
            raise ValidationFailed(
                "Capacity must be between 1 and 1000.", details={"field": "capacity"}
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name),
            code=normalise_code(code),
            school_level=school_level,
            capacity=capacity,
            class_teacher_id=class_teacher_id,
        )

    def rename(self, name: str) -> None:
        self.name = _name(name)

    def set_capacity(self, capacity: int | None) -> None:
        if capacity is not None and not 1 <= capacity <= 1000:
            raise ValidationFailed(
                "Capacity must be between 1 and 1000.", details={"field": "capacity"}
            )
        self.capacity = capacity

    def has_room(self, current_enrolment: int) -> bool:
        return self.capacity is None or current_enrolment < self.capacity


@dataclass(kw_only=True, eq=False)
class Subject(Entity):
    tenant_id: UUID
    name: str
    code: str
    school_level: SchoolLevelCode
    description: str = ""
    active: bool = True

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        code: str,
        school_level: SchoolLevelCode,
        description: str = "",
    ) -> Subject:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name),
            code=normalise_code(code),
            school_level=school_level,
            description=description.strip()[:2000],
        )


class EnrolmentStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


@dataclass(kw_only=True, eq=False)
class Enrolment(Entity):
    """A student's place in one class for one session.

    A student holds at most one active enrolment per session.
    """

    tenant_id: UUID
    student_id: UUID
    class_id: UUID
    session_id: UUID
    status: EnrolmentStatus = EnrolmentStatus.ACTIVE
    enrolled_at: datetime

    @classmethod
    def create(
        cls, *, tenant_id: UUID, student_id: UUID, class_id: UUID, session_id: UUID, now: datetime
    ) -> Enrolment:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            class_id=class_id,
            session_id=session_id,
            enrolled_at=now,
        )
