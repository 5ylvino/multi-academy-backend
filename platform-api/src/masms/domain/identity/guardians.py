"""Links between parents (guardians) and the students they look after.

A link is what lets a parent see a child's fees, results and attendance, and pay for them.
Only school staff create links; a parent can never link themselves to a child.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from masms.domain.identity.roles import SchoolRole
from masms.domain.identity.user import UserAccount
from masms.domain.shared.errors import ValidationFailed

MAX_GUARDIANS_PER_STUDENT = 6


class Relationship(StrEnum):
    MOTHER = "mother"
    FATHER = "father"
    GUARDIAN = "guardian"
    SPONSOR = "sponsor"  # pays fees but is not the legal guardian
    OTHER = "other"


def parse_relationship(value: str) -> Relationship:
    try:
        return Relationship(value)
    except ValueError as exc:
        raise ValidationFailed(
            "Relationship must be mother, father, guardian, sponsor or other.",
            details={"field": "relationship"},
        ) from exc


@dataclass(frozen=True, slots=True)
class GuardianLink:
    tenant_id: UUID
    parent_id: UUID
    student_id: UUID
    relationship: Relationship
    created_by: UUID
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        parent: UserAccount,
        student: UserAccount,
        relationship: Relationship,
        existing_count: int,
        by: UUID,
        now: datetime,
    ) -> GuardianLink:
        if parent.tenant_id is None or parent.tenant_id != student.tenant_id:
            raise ValidationFailed("Both people must belong to this school.")
        if SchoolRole.PARENT.value not in parent.roles:
            raise ValidationFailed(
                "That account is not a parent account.", details={"field": "parent_id"}
            )
        if SchoolRole.STUDENT.value not in student.roles:
            raise ValidationFailed(
                "That account is not a student account.", details={"field": "student_id"}
            )
        if existing_count >= MAX_GUARDIANS_PER_STUDENT:
            raise ValidationFailed(
                f"A student can have at most {MAX_GUARDIANS_PER_STUDENT} linked parents.",
                code="too_many_guardians",
            )
        return cls(
            tenant_id=parent.tenant_id,
            parent_id=parent.id,
            student_id=student.id,
            relationship=relationship,
            created_by=by,
            created_at=now,
        )
