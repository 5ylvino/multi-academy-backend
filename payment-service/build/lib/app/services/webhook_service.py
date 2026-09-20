from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.db.models import PaymentCheckoutSession, PaymentWebhookEvent
from app.providers.registry import gateway_registry
from app.services.control_client import control_client


class WebhookService:
    async def handle(
        self,
        db: Session,
        *,
        gateway_id: str,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> dict[str, str]:
        adapter = gateway_registry.get(gateway_id)
        if adapter is None:
            raise ValueError(f"Unknown gateway: {gateway_id}")

        parsed_preview = adapter.parse_webhook(raw_body)
        tenant_id = None
        secret_scope = "__platform__"

        if parsed_preview.reference:
            session = (
                db.query(PaymentCheckoutSession)
                .filter(PaymentCheckoutSession.reference == parsed_preview.reference)
                .one_or_none()
            )
            if session is not None:
                tenant_id = session.tenant_id
                secret_scope = (
                    "__platform__" if session.context_key == "saas_subscription" else session.tenant_id
                )

        vault = await control_client.get_provider_secrets(secret_scope, "payment")
        if not adapter.verify_webhook_signature(vault.get("secrets") or {}, headers, raw_body):
            raise PermissionError("Invalid webhook signature")

        parsed = adapter.parse_webhook(raw_body)
        if not parsed.reference:
            return {"status": "ignored"}

        event_key = hashlib.sha256(raw_body).hexdigest()
        existing = (
            db.query(PaymentWebhookEvent)
            .filter(PaymentWebhookEvent.event_key == event_key)
            .one_or_none()
        )
        if existing is not None:
            return {"status": "duplicate", "reference": parsed.reference}

        db.add(
            PaymentWebhookEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                gateway_id=gateway_id,
                event_key=event_key,
                reference=parsed.reference,
                status=parsed.status,
                payload_json={"reference": parsed.reference, "status": parsed.status},
            )
        )

        session = (
            db.query(PaymentCheckoutSession)
            .filter(PaymentCheckoutSession.reference == parsed.reference)
            .one_or_none()
        )
        if session and parsed.status == "success" and session.status != "settled":
            session.status = "settled"
            session.provider_reference = parsed.reference

        db.commit()
        return {
            "status": parsed.status,
            "reference": parsed.reference,
            "tenantId": tenant_id or "",
        }


webhook_service = WebhookService()
