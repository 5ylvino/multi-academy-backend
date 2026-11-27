"""Emails a student's guardians when a clinic visit ends with the child sent home or referred.

The in-app notice is written in the same transaction as the visit. This email is the second
channel for the outcomes a parent must act on now. It says what happened and nothing clinical.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from uuid import UUID

from app.domain.identity.user import UserStatus
from app.domain.operations.clinic import Outcome
from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory

_WHAT = {
    Outcome.SENT_HOME.value: "is being sent home from school",
    Outcome.REFERRED_TO_HOSPITAL.value: "has been referred to hospital",
}


class SendUrgentClinicEmail:
    """Handles ``operations.clinic_visit_closed``; does nothing unless the outcome is urgent."""

    def __init__(
        self, *, email: EmailSender, uow_factory: UnitOfWorkFactory, school_app_url: str
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._base = school_app_url.rstrip("/")

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        if not payload.get("urgent"):
            return
        what = _WHAT.get(str(payload.get("outcome")))
        if what is None:
            return
        tenant_id = UUID(str(payload["tenant_id"]))
        student_id = UUID(str(payload["student_id"]))
        visit_id = UUID(str(payload["visit_id"]))
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
            student = await uow.users.get(student_id)
            links = await uow.guardians.guardians_of(student_id)
            parents = [await uow.users.get(link.parent_id) for link in links]
        if tenant is None or student is None:
            return
        first = student.full_name.split()[0] if student.full_name else "Your child"
        link = f"{self._base}/clinic/visits/{visit_id}"
        lead = (
            f"{first} {what} after a visit to the clinic at {tenant.name}. "
            "Please contact the school as soon as you can."
        )
        for parent in parents:
            if parent is None or parent.status is not UserStatus.ACTIVE:
                continue
            await self._email.send(
                EmailMessage(
                    to=str(parent.email),
                    subject=f"{first} {what}",
                    text=f"{lead}\n\n{link}",
                    html=f'<p>{escape(lead)}</p><p><a href="{escape(link)}">See the visit</a></p>',
                )
            )
