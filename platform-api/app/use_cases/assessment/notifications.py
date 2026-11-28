"""Emails guardians when their child's report card is released.

The in-app notice is written in the same transaction as the release. The email is the channel
most families read. It says the card is ready and links to it; no scores leave the platform by
email.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from uuid import UUID

from app.domain.identity.user import UserStatus
from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory


class SendReportCardEmails:
    """Handles ``assessment.results_released``. Sends nothing if the release was withdrawn since."""

    def __init__(
        self, *, email: EmailSender, uow_factory: UnitOfWorkFactory, school_app_url: str
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._base = school_app_url.rstrip("/")

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        tenant_id = UUID(str(payload["tenant_id"]))
        term_id = UUID(str(payload["term_id"]))
        class_id = UUID(str(payload["class_id"]))
        student_ids = [UUID(str(s)) for s in payload.get("student_ids", [])]
        messages: list[EmailMessage] = []
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            release = await uow.assessment.get_release(term_id, class_id)
            tenant = await uow.tenants.get(tenant_id)
            term = await uow.academics.get_term(term_id)
            if release is None or not release.visible or tenant is None or term is None:
                return
            sent_to: set[tuple[UUID, UUID]] = set()
            for student_id in student_ids:
                student = await uow.users.get(student_id)
                if student is None:
                    continue
                first = student.full_name.split()[0] if student.full_name else "Your child"
                link = f"{self._base}/report-cards/{student_id}/{term_id}"
                lead = f"{first}'s {term.name} report card from {tenant.name} is ready to view."
                for guardian in await uow.guardians.guardians_of(student_id):
                    if (guardian.parent_id, student_id) in sent_to:
                        continue
                    parent = await uow.users.get(guardian.parent_id)
                    if parent is None or parent.status is not UserStatus.ACTIVE:
                        continue
                    sent_to.add((guardian.parent_id, student_id))
                    messages.append(
                        EmailMessage(
                            to=str(parent.email),
                            subject=f"{first}'s {term.name} report card is ready",
                            text=f"{lead}\n\n{link}",
                            html=(
                                f"<p>{escape(lead)}</p>"
                                f'<p><a href="{escape(link)}">Open the report card</a></p>'
                            ),
                        )
                    )
        for message in messages:
            await self._email.send(message)
