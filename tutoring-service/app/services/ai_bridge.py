from __future__ import annotations

import time
import uuid

import httpx
from jose import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TutoringBooking, TutoringSession
from app.db.session import SessionLocal
from app.schemas.tutoring import AiPrepResponse
from app.security.context import RequestContext


def _ai_service_token(*, tenant_id: str, actor_id: str) -> str:
    cfg = get_settings()
    secret = cfg.ai_service_jwt_secret or cfg.service_jwt_secret
    now = int(time.time())
    payload = {
        "iss": cfg.service_jwt_issuer,
        "aud": "mas-ai-service",
        "sub": "service",
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "roles": ["student"],
        "features": ["ai.tutor"],
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class AiBridgeService:
    async def start_ai_prep(
        self,
        ctx: RequestContext,
        booking_id: str,
        *,
        db: Session | None = None,
    ) -> AiPrepResponse:
        cfg = get_settings()
        if not cfg.ai_service_url:
            raise RuntimeError("AI_SERVICE_URL is not configured")
        session = db or SessionLocal()
        owns = db is None
        try:
            booking = (
                session.query(TutoringBooking)
                .filter(
                    TutoringBooking.id == uuid.UUID(booking_id),
                    TutoringBooking.tenant_id == ctx.tenant_id,
                )
                .one_or_none()
            )
            if booking is None:
                raise ValueError("Booking not found")
            student_id = booking.student_id or ctx.actor_id
            topic = booking.topic or booking.subject
            token = _ai_service_token(tenant_id=ctx.tenant_id, actor_id=student_id)
            url = f"{cfg.ai_service_url.rstrip('/')}/v1/tutor/chat"
            body = {
                "message": f"Prepare me for a tutoring session on {topic}. Give a short study guide.",
                "topic": topic,
            }
            async with httpx.AsyncClient(timeout=45) as client:
                res = await client.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Tenant-Id": ctx.tenant_id,
                        "Content-Type": "application/json",
                    },
                )
                res.raise_for_status()
                data = res.json()
            tutoring_session = TutoringSession(
                id=uuid.uuid4(),
                booking_id=booking.id,
                tenant_id=ctx.tenant_id,
                ai_session_id=data.get("sessionId"),
                ai_prep_summary=str(data.get("content") or "")[:4000],
                status="ai_prep_ready",
            )
            session.add(tutoring_session)
            session.commit()
            return AiPrepResponse(
                sessionId=str(tutoring_session.id),
                aiSessionId=tutoring_session.ai_session_id,
                prepSummary=tutoring_session.ai_prep_summary or "",
                bookingId=str(booking.id),
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()
