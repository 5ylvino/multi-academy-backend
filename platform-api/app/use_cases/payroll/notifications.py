"""Tells staff their payslip is ready once a pay run is marked paid. Run by the worker.

The email carries no amounts: pay is private, and email is not. Staff open the payslip in the
school app after signing in.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from html import escape
from typing import Any
from uuid import UUID

from app.domain.identity.user import UserStatus
from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory


class SendPayslipReadyEmails:
    """Handles ``payroll.run_paid``."""

    def __init__(
        self, *, email: EmailSender, uow_factory: UnitOfWorkFactory, school_app_url: str
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._url = school_app_url.rstrip("/") + "/payslips"

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        tenant_id = UUID(str(payload["tenant_id"]))
        staff_ids = [UUID(str(s)) for s in payload.get("staff_ids", [])]
        period = f"{calendar.month_name[int(payload['month'])]} {int(payload['year'])}"
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
            people = [await uow.users.get(staff_id) for staff_id in staff_ids]
        if tenant is None:
            return
        for person in people:
            if person is None or person.status is not UserStatus.ACTIVE:
                continue
            first = person.full_name.split()[0] if person.full_name.strip() else "there"
            lead = (
                f"Your {period} payslip from {tenant.name} is ready. Sign in to see the "
                "details of your pay and deductions."
            )
            await self._email.send(
                EmailMessage(
                    to=str(person.email),
                    subject=f"Your {period} payslip is ready",
                    text=f"Hello {first},\n\n{lead}\n\n{self._url}",
                    html=(
                        f"<p>Hello {escape(first)},</p><p>{escape(lead)}</p>"
                        f'<p><a href="{escape(self._url)}">Open my payslips</a></p>'
                    ),
                )
            )
