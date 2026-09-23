from datetime import datetime, timedelta, timezone
import secrets

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import StaffContext, client_ip, get_current_staff
from app.models import PlatformUser, SecuritySettings, StaffSession
from app.rbac import permissions_for_role
from app.schemas import (
    LoginRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    RefreshRequest,
    StaffSessionOut,
    StaffUserOut,
    TokenResponse,
)
from app.security import (
    create_staff_token,
    decode_staff_token,
    generate_totp_secret,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _user_out(user: PlatformUser) -> StaffUserOut:
    out = StaffUserOut.model_validate(user)
    out.permissions = permissions_for_role(user.role)
    return out


def _security_settings(db: Session) -> SecuritySettings:
    row = db.get(SecuritySettings, 1)
    if row is None:
        row = SecuritySettings(id=1)
        db.add(row)
        db.flush()
    return row


def _create_session(
    db: Session,
    user: PlatformUser,
    *,
    ip: str,
    user_agent: str,
) -> tuple[TokenResponse, StaffSession]:
    settings = get_settings()
    access_jti = secrets.token_urlsafe(16)
    refresh_jti = secrets.token_urlsafe(16)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    session = StaffSession(
        user_id=user.id,
        jti=access_jti,
        refresh_jti=refresh_jti,
        ip_address=ip,
        user_agent=(user_agent or "")[:512],
        expires_at=expires,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.flush()
    perms = permissions_for_role(user.role)
    tokens = TokenResponse(
        access_token=create_staff_token(
            user.id, user.email, user.role, perms, "access", jti=access_jti, session_jti=access_jti
        ),
        refresh_token=create_staff_token(
            user.id, user.email, user.role, perms, "refresh", jti=refresh_jti, session_jti=access_jti
        ),
        user=_user_out(user),
    )
    return tokens, session


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.execute(
        select(PlatformUser).where(PlatformUser.email == body.email.lower())
    ).scalar_one_or_none()

    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if user is None or not user.is_active:
        raise generic

    now = datetime.now(timezone.utc)
    if user.locked_until is not None:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked")

    if not verify_password(body.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
        db.commit()
        raise generic

    # When MFA is required org-wide, users without TOTP may still sign in once
    # to complete /v1/auth/mfa/setup + verify. After enrollment, codes are required.
    if user.mfa_enabled:
        if not body.mfa_code:
            raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, "MFA code required")
        if not verify_totp(user.mfa_totp_secret or "", body.mfa_code):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")
    else:
        _security_settings(db)  # ensure policy row exists for settings UI

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    ua = request.headers.get("user-agent", "")
    tokens, _ = _create_session(db, user, ip=client_ip(request), user_agent=ua)
    record_audit(
        db,
        action="auth.login",
        actor_id=str(user.id),
        actor_email=user.email,
        ip_address=client_ip(request),
    )
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    try:
        payload = decode_staff_token(body.refresh_token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token required")

    refresh_jti = payload.get("jti")
    session = None
    if refresh_jti:
        session = db.execute(
            select(StaffSession).where(StaffSession.refresh_jti == refresh_jti)
        ).scalar_one_or_none()
        if session is None or session.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
        now = datetime.now(timezone.utc)
        policy = db.get(SecuritySettings, 1)
        settings = get_settings()
        idle_minutes = (
            policy.idle_timeout_minutes
            if policy and policy.idle_timeout_minutes
            else settings.staff_idle_timeout_minutes
        )
        exp = session.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
        last_seen = session.last_seen_at
        if (
            last_seen is not None
            and idle_minutes
            and now - (
                last_seen.replace(tzinfo=timezone.utc)
                if last_seen.tzinfo is None
                else last_seen
            )
            > timedelta(minutes=idle_minutes)
        ):
            session.revoked_at = now
            session.revoke_reason = "idle_timeout"
            db.commit()
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"Session idle for more than {idle_minutes} minutes",
            )

    user = db.get(PlatformUser, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")

    # Rotate: revoke old session, issue new pair
    if session is not None:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "rotated"
    ua = request.headers.get("user-agent", "")
    tokens, _ = _create_session(db, user, ip=client_ip(request), user_agent=ua)
    db.commit()
    return tokens


@router.get("/me", response_model=StaffUserOut)
def me(staff: StaffContext = Depends(get_current_staff)):
    return _user_out(staff.user)


@router.get("/session-policy")
def session_policy(
    _: StaffContext = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    policy = db.get(SecuritySettings, 1)
    settings = get_settings()
    return {
        "idle_timeout_minutes": (
            policy.idle_timeout_minutes
            if policy and policy.idle_timeout_minutes
            else settings.staff_idle_timeout_minutes
        ),
    }


@router.get("/sessions", response_model=list[StaffSessionOut])
def list_my_sessions(
    staff: StaffContext = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(StaffSession)
        .where(StaffSession.user_id == staff.user.id)
        .order_by(StaffSession.created_at.desc())
        .limit(50)
    ).scalars().all()
    return rows


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_my_session(
    session_id: int,
    staff: StaffContext = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    session = db.get(StaffSession, session_id)
    if session is None or session.user_id != staff.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "user_revoke"
        record_audit(
            db,
            action="auth.session.revoke",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="staff_session",
            target_id=str(session_id),
        )
        db.commit()


@router.post("/mfa/setup", response_model=MfaSetupResponse)
def mfa_setup(staff: StaffContext = Depends(get_current_staff), db: Session = Depends(get_db)):
    secret = generate_totp_secret()
    staff.user.mfa_totp_secret = secret
    staff.user.mfa_enabled = False  # enabled only after verification
    db.commit()
    return MfaSetupResponse(secret=secret, otpauth_uri=totp_provisioning_uri(secret, staff.email))


@router.post("/mfa/verify")
def mfa_verify(
    body: MfaVerifyRequest,
    staff: StaffContext = Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    if not staff.user.mfa_totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run MFA setup first")
    if not verify_totp(staff.user.mfa_totp_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid code")
    staff.user.mfa_enabled = True
    record_audit(db, action="auth.mfa_enabled", actor_id=staff.id, actor_email=staff.email)
    db.commit()
    return {"mfaEnabled": True}
