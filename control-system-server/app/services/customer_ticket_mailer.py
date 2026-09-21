import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CustomerTicket, ProviderConfig
from app.security import decrypt_secret

logger = logging.getLogger(__name__)


def _provider(db: Session):
    config = db.execute(
        select(ProviderConfig)
        .options(selectinload(ProviderConfig.secrets))
        .where(
            ProviderConfig.capability == "email",
            ProviderConfig.tenant_id.is_(None),
            ProviderConfig.is_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if not config:
        return None, {}
    return config, {
        secret.key: decrypt_secret(secret.ciphertext)
        for secret in config.secrets
        if secret.is_active
    }


def _sender(config: ProviderConfig | None, secrets: dict[str, str]) -> str:
    settings = config.settings if config else {}
    return (
        str(settings.get("from") or "").strip()
        or str(settings.get("fromEmail") or settings.get("from_email") or "").strip()
        or secrets.get("from_email", "").strip()
        or secrets.get("from", "").strip()
        or os.getenv("EMAIL_FROM_ADDRESS", "").strip()
    )


def _sender_name(config: ProviderConfig | None, secrets: dict[str, str]) -> str:
    settings = config.settings if config else {}
    return (
        str(settings.get("fromName") or settings.get("from_name") or "").strip()
        or secrets.get("from_name", "").strip()
        or os.getenv("EMAIL_FROM_NAME", "").strip()
        or "MA-SMS"
    )


def _send_resend(
    recipient: str,
    subject: str,
    text: str,
    sender: str,
    api_key: str,
) -> None:
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": sender, "to": [recipient], "subject": subject, "text": text},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"Resend rejected email ({response.status_code})")


def _send_smtp(
    recipient: str,
    subject: str,
    text: str,
    sender: str,
    secrets: dict[str, str],
    settings: dict,
    sender_name: str,
) -> None:
    host = (
        secrets.get("host")
        or secrets.get("smtp_host")
        or str(settings.get("host") or settings.get("smtp_host") or "")
    )
    username = (
        secrets.get("username")
        or secrets.get("email")
        or str(settings.get("username") or settings.get("email") or "")
        or sender
    )
    password = secrets.get("password") or secrets.get("smtp_password") or ""
    port = int(settings.get("port") or secrets.get("port") or 587)
    if not host or not username or not password:
        raise RuntimeError("SMTP email provider is missing host, username, or password")

    message = EmailMessage()
    message["From"] = formataddr((sender_name, sender or username))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    # cPanel commonly uses implicit TLS on 465, while 587 uses STARTTLS.
    # The provider health check supports both modes; delivery must use the
    # same transport or a health check can pass while real mail fails.
    secure = settings.get("secure")
    if secure is None:
        secure = secrets.get("secure")
    if port == 465 or str(secure or "").lower() in {"1", "true", "yes"}:
        with smtplib.SMTP_SSL(
            host,
            port,
            timeout=15,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(message)


def send_customer_email(
    db: Session,
    ticket: CustomerTicket,
    subject: str,
    text: str,
) -> bool:
    """Send a customer-facing ticket email using the configured global provider.

    Ticket persistence is deliberately independent from email delivery. A
    provider outage must not lose a lead; the failure is logged for retry or
    operational follow-up.
    """
    config, secrets = _provider(db)
    provider_id = config.provider_id if config else ""
    settings = config.settings if config else {}
    sender = _sender(config, secrets)
    sender_name = _sender_name(config, secrets)
    api_key = secrets.get("api_key") or os.getenv("EMAIL_API_KEY", "").strip()

    try:
        if provider_id == "resend" and api_key and sender:
            _send_resend(
                ticket.email,
                subject,
                text,
                formataddr((sender_name, sender)),
                api_key,
            )
        elif provider_id in {"cpanel", "smtp"}:
            _send_smtp(
                ticket.email,
                subject,
                text,
                sender,
                secrets,
                settings,
                sender_name,
            )
        else:
            raise RuntimeError(
                "No usable email provider configured; configure the global email provider"
            )
        logger.info(
            "Customer ticket email sent ticket=%s provider=%s recipient=%s",
            ticket.ticket_number,
            provider_id,
            "redacted",
        )
        return True
    except Exception:
        logger.exception("Customer ticket email delivery failed ticket=%s", ticket.ticket_number)
        return False
