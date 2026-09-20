from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.control_client import control_client
from app.db.models import TutorProfile, TutoringBooking, TutoringPayment, TutoringSession
from app.db.session import SessionLocal
from app.schemas.tutoring import (
    BookingCreateRequest,
    BookingListItem,
    BookingListResponse,
    BookingResponse,
    PaymentIntentResponse,
)
from app.security.context import RequestContext
from app.security.roles import is_teacher_role


class BookingService:
    def list_mine(
        self,
        ctx: RequestContext,
        *,
        db: Session | None = None,
    ) -> BookingListResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            tutor = (
                session.query(TutorProfile)
                .filter(
                    TutorProfile.tenant_id == ctx.tenant_id,
                    TutorProfile.user_id == ctx.actor_id,
                )
                .one_or_none()
            )
            query = session.query(TutoringBooking).filter(TutoringBooking.tenant_id == ctx.tenant_id)
            if tutor is not None and is_teacher_role(ctx.roles):
                query = query.filter(TutoringBooking.tutor_id == tutor.id)
            elif "parent" in ctx.roles:
                query = query.filter(TutoringBooking.parent_id == ctx.actor_id)
            elif "student" in ctx.roles:
                query = query.filter(TutoringBooking.student_id == ctx.actor_id)
            elif "director" in ctx.roles or "school_admin" in ctx.roles:
                pass
            else:
                query = query.filter(
                    (TutoringBooking.parent_id == ctx.actor_id)
                    | (TutoringBooking.student_id == ctx.actor_id)
                )
            rows = query.order_by(TutoringBooking.created_at.desc()).limit(50).all()
            items: list[BookingListItem] = []
            for booking in rows:
                tutor_row = (
                    session.query(TutorProfile).filter(TutorProfile.id == booking.tutor_id).one_or_none()
                )
                payment = (
                    session.query(TutoringPayment)
                    .filter(TutoringPayment.booking_id == booking.id)
                    .order_by(TutoringPayment.created_at.desc())
                    .first()
                )
                tutoring_session = (
                    session.query(TutoringSession)
                    .filter(TutoringSession.booking_id == booking.id)
                    .order_by(TutoringSession.created_at.desc())
                    .first()
                )
                items.append(
                    BookingListItem(
                        bookingId=str(booking.id),
                        tutorId=str(booking.tutor_id),
                        tutorName=tutor_row.display_name if tutor_row else "Tutor",
                        studentId=booking.student_id,
                        status=booking.status,
                        subject=booking.subject,
                        topic=booking.topic,
                        scheduledAt=booking.scheduled_at,
                        includeAiPrep=booking.include_ai_prep,
                        paymentId=str(payment.id) if payment else None,
                        paymentStatus=payment.status if payment else None,
                        amount=payment.amount if payment else None,
                        currency=payment.currency if payment else None,
                        aiPrepSummary=tutoring_session.ai_prep_summary if tutoring_session else None,
                    )
                )
            return BookingListResponse(items=items, total=len(items))
        finally:
            if owns:
                session.close()

    def create(
        self,
        ctx: RequestContext,
        payload: BookingCreateRequest,
        *,
        db: Session | None = None,
    ) -> BookingResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            tutor = (
                session.query(TutorProfile)
                .filter(
                    TutorProfile.id == uuid.UUID(payload.tutor_id),
                    TutorProfile.tenant_id == ctx.tenant_id,
                    TutorProfile.status == "active",
                )
                .one_or_none()
            )
            if tutor is None:
                raise ValueError("Tutor not found")
            booking = TutoringBooking(
                id=uuid.uuid4(),
                tenant_id=ctx.tenant_id,
                tutor_id=tutor.id,
                student_id=payload.student_id,
                parent_id=payload.parent_id or ctx.actor_id,
                external_guest_name=payload.external_guest_name,
                subject=payload.subject,
                topic=payload.topic,
                scheduled_at=payload.scheduled_at,
                duration_minutes=payload.duration_minutes,
                include_ai_prep=payload.include_ai_prep,
                status="requested",
                notes=payload.notes,
            )
            session.add(booking)
            session.commit()
            session.refresh(booking)
            return BookingResponse(
                bookingId=str(booking.id),
                tutorId=str(booking.tutor_id),
                status=booking.status,
                subject=booking.subject,
                topic=booking.topic,
                scheduledAt=booking.scheduled_at,
                includeAiPrep=booking.include_ai_prep,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def confirm(
        self,
        ctx: RequestContext,
        booking_id: str,
        *,
        db: Session | None = None,
    ) -> BookingResponse:
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
            tutor = session.query(TutorProfile).filter(TutorProfile.id == booking.tutor_id).one()
            if tutor.user_id != ctx.actor_id and "director" not in ctx.roles:
                raise PermissionError("Only the assigned tutor can confirm")
            booking.status = "confirmed"
            session.commit()
            return BookingResponse(
                bookingId=str(booking.id),
                tutorId=str(booking.tutor_id),
                status=booking.status,
                subject=booking.subject,
                topic=booking.topic,
                scheduledAt=booking.scheduled_at,
                includeAiPrep=booking.include_ai_prep,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    async def create_payment_intent(
        self,
        ctx: RequestContext,
        booking_id: str,
        *,
        db: Session | None = None,
    ) -> PaymentIntentResponse:
        session = db or SessionLocal()
        owns = db is None
        cfg = get_settings()
        policy = await control_client.get_tutoring_policy(ctx.tenant_id)
        platform_fee_percent = float(policy.get("platformFeePercent", cfg.platform_fee_percent))
        default_currency = str(policy.get("defaultCurrency") or cfg.default_currency)
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
            tutor = session.query(TutorProfile).filter(TutorProfile.id == booking.tutor_id).one()
            hours = max(booking.duration_minutes / 60, 0.5)
            amount = round(tutor.hourly_rate * hours, 2)
            platform_fee = round(amount * (platform_fee_percent / 100), 2)
            tutor_payout = round(amount - platform_fee, 2)
            payment = TutoringPayment(
                id=uuid.uuid4(),
                booking_id=booking.id,
                tenant_id=ctx.tenant_id,
                amount=amount,
                platform_fee=platform_fee,
                tutor_payout=tutor_payout,
                currency=tutor.currency or default_currency,
                status="pending",
                provider_ref=f"mas-tutor-{uuid.uuid4().hex[:12]}",
            )
            session.add(payment)
            session.commit()
            return PaymentIntentResponse(
                paymentId=str(payment.id),
                amount=payment.amount,
                platformFee=payment.platform_fee,
                tutorPayout=payment.tutor_payout,
                currency=payment.currency,
                status=payment.status,
                providerRef=payment.provider_ref,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def settle_payment(
        self,
        ctx: RequestContext,
        payment_id: str,
        *,
        provider_reference: str | None = None,
        checkout_reference: str | None = None,
        db: Session | None = None,
    ) -> PaymentIntentResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            payment = (
                session.query(TutoringPayment)
                .filter(
                    TutoringPayment.id == uuid.UUID(payment_id),
                    TutoringPayment.tenant_id == ctx.tenant_id,
                )
                .one_or_none()
            )
            if payment is None:
                raise ValueError("Payment not found")
            if payment.status == "paid":
                return PaymentIntentResponse(
                    paymentId=str(payment.id),
                    amount=payment.amount,
                    platformFee=payment.platform_fee,
                    tutorPayout=payment.tutor_payout,
                    currency=payment.currency,
                    status=payment.status,
                    providerRef=payment.provider_ref,
                )
            verified_reference = provider_reference or checkout_reference
            if not verified_reference:
                raise ValueError("A verified provider or checkout reference is required")
            payment.provider_ref = verified_reference
            payment.status = "paid"
            booking = session.query(TutoringBooking).filter(TutoringBooking.id == payment.booking_id).one()
            booking.status = "paid"
            session.commit()
            return PaymentIntentResponse(
                paymentId=str(payment.id),
                amount=payment.amount,
                platformFee=payment.platform_fee,
                tutorPayout=payment.tutor_payout,
                currency=payment.currency,
                status=payment.status,
                providerRef=payment.provider_ref,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()
