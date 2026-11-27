"""Event handlers that send tenancy emails. Run by the worker from the outbox."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any

from masms.use_cases.shared.ports import EmailMessage, EmailSender


class SendVerificationCodeEmail:
    """Handles ``tenancy.verification_code_issued``."""

    def __init__(self, *, email: EmailSender) -> None:
        self._email = email

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        school = str(payload["school_name"])
        code = str(payload["code"])
        text = (
            f"Welcome to MA-SMS.\n\nYour verification code for {school} is {code}.\n"
            "It expires in 30 minutes. If you did not sign up, you can ignore this email."
        )
        html = (
            "<p>Welcome to MA-SMS.</p>"
            f"<p>Your verification code for <strong>{escape(school)}</strong> is</p>"
            f'<p style="font-size:24px;letter-spacing:4px"><strong>{escape(code)}</strong></p>'
            "<p>It expires in 30 minutes. If you did not sign up, you can ignore this email.</p>"
        )
        await self._email.send(
            EmailMessage(
                to=str(payload["email"]),
                subject=f"{code} is your MA-SMS code",
                text=text,
                html=html,
            )
        )
