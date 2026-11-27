"""Emails an urgent announcement to everyone in its audience. Run by the worker.

Delivery is at least once: if the worker stops part way, a retry may send some people the alert
twice, which is the right trade for an emergency notice.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from uuid import UUID

from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory


class SendUrgentAnnouncement:
    """Handles ``communication.urgent_announcement``."""

    def __init__(
        self, *, email: EmailSender, uow_factory: UnitOfWorkFactory, school_app_url: str
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._base = school_app_url.rstrip("/")

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        tenant_id = UUID(str(payload["tenant_id"]))
        announcement_id = UUID(str(payload["announcement_id"]))
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
            announcement = await uow.communication.get_announcement(announcement_id)
            if tenant is None or announcement is None or announcement.withdrawn_at is not None:
                return
            term = await uow.academics.current_term()
            contacts = await uow.communication.audience_contacts(
                announcement.audience, session_id=term.session_id if term else None
            )
        link = f"{self._base}/announcements/{announcement.id}"
        subject = f"URGENT from {tenant.name}: {announcement.title}"
        for person in contacts:
            first = person.full_name.split()[0] if person.full_name.strip() else "there"
            text = (
                f"Hello {first},\n\n{announcement.title}\n\n{announcement.body}\n\n"
                f"Please confirm you have read this:\n{link}"
            )
            body_html = "<br>".join(escape(line) for line in announcement.body.splitlines())
            html = (
                f"<p>Hello {escape(first)},</p><h2>{escape(announcement.title)}</h2>"
                f"<p>{body_html}</p>"
                f'<p><a href="{escape(link)}">Confirm you have read this</a></p>'
            )
            await self._email.send(
                EmailMessage(to=person.email, subject=subject, text=text, html=html)
            )
