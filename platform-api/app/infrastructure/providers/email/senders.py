"""Email adapters."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, make_msgid

import aiosmtplib
import structlog

from app.use_cases.shared.ports import EmailMessage

_log = structlog.get_logger(__name__)


class LogEmailSender:
    """Development only: writes the email to the log instead of sending it."""

    async def send(self, message: EmailMessage) -> None:
        _log.info("email.logged", to=message.to, subject=message.subject, body=message.text)


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        start_tls: bool,
        from_address: str,
        from_name: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._start_tls = start_tls
        self._from = formataddr((from_name, from_address))
        self._domain = from_address.split("@", 1)[-1]
        self._timeout = timeout_seconds

    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = make_msgid(domain=self._domain)
        mime.set_content(message.text)
        if message.html:
            mime.add_alternative(message.html, subtype="html")
        await aiosmtplib.send(
            mime,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            start_tls=self._start_tls if self._port != 465 else False,
            use_tls=self._port == 465,
            timeout=self._timeout,
        )
        _log.info("email.sent", to_domain=message.to.split("@")[-1], subject=message.subject)
