"""Emails the school's safeguarding leads when an urgent concern is recorded. Run by the worker.

The email says only that there is an urgent concern and links to it: no names, no details.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from uuid import UUID

from app.use_cases.safeguarding.cases import leads_of
from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory


class SendUrgentConcernAlert:
    """Handles ``safeguarding.urgent_concern``."""

    def __init__(
        self, *, email: EmailSender, uow_factory: UnitOfWorkFactory, school_app_url: str
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._base = school_app_url.rstrip("/")

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        tenant_id = UUID(str(payload["tenant_id"]))
        concern_id = UUID(str(payload["concern_id"]))
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
            leads = await leads_of(uow, tenant_id)
        if tenant is None:
            return
        link = f"{self._base}/safeguarding/{concern_id}"
        lead = (
            f"An urgent safeguarding concern has just been recorded at {tenant.name}. "
            "A child may be at immediate risk. Please review it now."
        )
        for person in leads:
            await self._email.send(
                EmailMessage(
                    to=str(person.email),
                    subject=f"URGENT safeguarding concern at {tenant.name}",
                    text=f"{lead}\n\n{link}",
                    html=f'<p>{escape(lead)}</p><p><a href="{escape(link)}">Open the case</a></p>',
                )
            )
