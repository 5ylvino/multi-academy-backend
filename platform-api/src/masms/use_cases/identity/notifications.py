"""Event handlers that send identity emails."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from urllib.parse import quote

from masms.use_cases.shared.ports import EmailMessage, EmailSender


class SendInviteEmail:
    """Handles ``identity.user_invited``."""

    def __init__(self, *, email: EmailSender, school_app_url: str) -> None:
        self._email = email
        self._base = school_app_url.rstrip("/")

    async def __call__(self, payload: Mapping[str, Any]) -> None:
        slug = quote(str(payload["school_slug"]))
        token = quote(str(payload["token"]))
        link = f"{self._base}/{slug}/accept-invite?token={token}"
        school = str(payload["school_name"])
        name = str(payload["full_name"])
        text = (
            f"Hello {name},\n\n{school} has invited you to MA-SMS.\n"
            f"Set your password here (the link works for 7 days):\n{link}\n"
        )
        html = (
            f"<p>Hello {escape(name)},</p><p>{escape(school)} has invited you to MA-SMS.</p>"
            f'<p><a href="{escape(link)}">Set your password</a> (the link works for 7 days).</p>'
        )
        await self._email.send(
            EmailMessage(
                to=str(payload["email"]),
                subject=f"You're invited to {school} on MA-SMS",
                text=text,
                html=html,
            )
        )
