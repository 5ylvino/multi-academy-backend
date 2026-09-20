from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import TutorProfile
from app.db.session import SessionLocal
from app.schemas.tutoring import (
    TutorProfileResponse,
    TutorRegisterRequest,
    TutorSearchResponse,
    TutorUpdateRequest,
)
from app.security.context import RequestContext
from app.security.roles import is_teacher_role


class TutorService:
    def register(
        self,
        ctx: RequestContext,
        payload: TutorRegisterRequest,
        *,
        db: Session | None = None,
    ) -> TutorProfileResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            existing = (
                session.query(TutorProfile)
                .filter(
                    TutorProfile.tenant_id == ctx.tenant_id,
                    TutorProfile.user_id == ctx.actor_id,
                )
                .one_or_none()
            )
            is_teacher = is_teacher_role(ctx.roles)
            if existing:
                existing.display_name = payload.display_name
                existing.bio = payload.bio
                existing.subjects_json = payload.subjects
                existing.hourly_rate = payload.hourly_rate
                existing.currency = payload.currency
                existing.visibility = payload.visibility
                existing.is_external = payload.is_external
                existing.is_school_teacher = is_teacher
                row = existing
            else:
                row = TutorProfile(
                    id=uuid.uuid4(),
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.actor_id,
                    display_name=payload.display_name,
                    bio=payload.bio,
                    subjects_json=payload.subjects,
                    hourly_rate=payload.hourly_rate,
                    currency=payload.currency,
                    visibility=payload.visibility,
                    is_school_teacher=is_teacher,
                    is_external=payload.is_external,
                    status="active",
                )
                session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_response(row)
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def search(
        self,
        ctx: RequestContext,
        *,
        subject: str | None = None,
        include_external: bool = True,
        db: Session | None = None,
    ) -> TutorSearchResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            query = session.query(TutorProfile).filter(
                TutorProfile.tenant_id == ctx.tenant_id,
                TutorProfile.status == "active",
            )
            if not include_external:
                query = query.filter(TutorProfile.is_external.is_(False))
            rows = query.order_by(TutorProfile.rating_avg.desc()).limit(50).all()
            if subject:
                subject_lower = subject.lower()
                rows = [
                    r
                    for r in rows
                    if any(subject_lower in str(s).lower() for s in (r.subjects_json or []))
                ]
            items = [self._to_response(r) for r in rows]
            return TutorSearchResponse(items=items, total=len(items))
        finally:
            if owns:
                session.close()

    def update(
        self,
        ctx: RequestContext,
        tutor_id: str,
        payload: TutorUpdateRequest,
        *,
        db: Session | None = None,
    ) -> TutorProfileResponse:
        session = db or SessionLocal()
        owns = db is None
        try:
            row = (
                session.query(TutorProfile)
                .filter(
                    TutorProfile.id == uuid.UUID(tutor_id),
                    TutorProfile.tenant_id == ctx.tenant_id,
                )
                .one_or_none()
            )
            if row is None:
                raise ValueError("Tutor not found")
            if row.user_id != ctx.actor_id and "director" not in ctx.roles:
                raise PermissionError("Only the tutor owner can update this profile")
            if payload.display_name is not None:
                row.display_name = payload.display_name
            if payload.bio is not None:
                row.bio = payload.bio
            if payload.subjects is not None:
                row.subjects_json = payload.subjects
            if payload.hourly_rate is not None:
                row.hourly_rate = payload.hourly_rate
            if payload.visibility is not None:
                row.visibility = payload.visibility
            session.commit()
            session.refresh(row)
            return self._to_response(row)
        except Exception:
            session.rollback()
            raise
        finally:
            if owns:
                session.close()

    def get_mine(self, ctx: RequestContext, *, db: Session | None = None) -> TutorProfileResponse | None:
        session = db or SessionLocal()
        owns = db is None
        try:
            row = (
                session.query(TutorProfile)
                .filter(
                    TutorProfile.tenant_id == ctx.tenant_id,
                    TutorProfile.user_id == ctx.actor_id,
                )
                .one_or_none()
            )
            return self._to_response(row) if row else None
        finally:
            if owns:
                session.close()

    def _to_response(self, row: TutorProfile) -> TutorProfileResponse:
        return TutorProfileResponse(
            tutorId=str(row.id),
            userId=row.user_id,
            displayName=row.display_name,
            bio=row.bio,
            subjects=list(row.subjects_json or []),
            hourlyRate=row.hourly_rate,
            currency=row.currency,
            visibility=row.visibility,
            isSchoolTeacher=row.is_school_teacher,
            isExternal=row.is_external,
            status=row.status,
            ratingAvg=row.rating_avg,
            sessionCount=row.session_count,
        )
