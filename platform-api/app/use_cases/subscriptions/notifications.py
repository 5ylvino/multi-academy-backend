"""Emails about platform invoices, sent to the school's contact address. Run by the worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from html import escape
from typing import Any
from uuid import UUID

from app.domain.shared.money import Money
from app.use_cases.shared.ports import EmailMessage, EmailSender, Scope, UnitOfWorkFactory


def _when(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _day(value: Any) -> str:
    return _when(value).strftime("%d %B %Y").lstrip("0")


class SendPlatformInvoiceEmail:
    """Handles ``subscriptions.invoice_issued``, ``invoice_overdue`` and ``invoice_paid``."""

    def __init__(
        self,
        *,
        email: EmailSender,
        uow_factory: UnitOfWorkFactory,
        pay_link: Callable[[UUID], str],
        bank_transfer_details: str | None,
    ) -> None:
        self._email = email
        self._uow_factory = uow_factory
        self._pay_link = pay_link
        self._bank = bank_transfer_details

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        tenant_id = UUID(str(payload["tenant_id"]))
        async with self._uow_factory(Scope.registry(), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            return
        invoice_id = UUID(str(payload["invoice_id"]))
        number = str(payload["number"])
        amount = str(Money(int(payload["total_minor"]), str(payload["currency"])))
        link = self._pay_link(invoice_id)
        school = tenant.name

        if "suspends_at" in payload:
            subject = f"Overdue: MA-SMS invoice {number} for {school}"
            lead = (
                f"Invoice {number} for {amount} was due on {_day(payload['due_at'])} and is "
                f"still unpaid. To keep {school} running, please pay by "
                f"{_day(payload['suspends_at'])}; after that the account is paused until it is "
                "paid. Paying turns it back on straight away."
            )
        elif "period_start" in payload:
            subject = f"MA-SMS invoice {number} for {school}"
            lead = (
                f"Your invoice {number} for {amount} covers {_day(payload['period_start'])} to "
                f"{_day(payload['period_end'])}. It is due on {_day(payload['due_at'])}."
            )
        else:
            subject = f"Payment received: MA-SMS invoice {number}"
            lead = f"Thank you. We received {amount} for invoice {number}. No action is needed."
            link = ""

        bank = (
            f"\n\nOr pay by bank transfer to {self._bank}, using {number} as the reference."
            if self._bank and link
            else ""
        )
        text = f"Hello,\n\n{lead}"
        html = f"<p>Hello,</p><p>{escape(lead)}</p>"
        if link:
            text += f"\n\nPay online here (no sign-in needed):\n{link}"
            html += (
                f'<p><a href="{escape(link)}">View and pay the invoice</a> (no sign-in needed)</p>'
            )
        if bank:
            text += bank
            html += f"<p>{escape(bank.strip())}</p>"
        await self._email.send(
            EmailMessage(to=tenant.contact_email, subject=subject, text=text, html=html)
        )
