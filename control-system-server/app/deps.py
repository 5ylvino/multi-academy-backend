from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import PlatformUser, SecuritySettings, ServiceClient, StaffSession, SupportSession
from app.rbac import permissions_for_role
from app.security import decode_m2m_token, decode_staff_token

bearer = HTTPBearer(auto_error=False)

# Reachable without MFA enrolment when the org policy requires it, so an
# operator can actually enrol instead of being locked out of their own console.
_MFA_EXEMPT_PATHS = (
    "/v1/auth/mfa/setup",
    "/v1/auth/mfa/verify",
    "/v1/auth/logout",
    "/v1/auth/me",
    "/v1/auth/refresh",
    "/v1/settings/security",
)


class StaffContext:
    def __init__(self, user: PlatformUser, permissions: list[str], session_id: int | None = None):
        self.user = user
        self.permissions = permissions
        self.session_id = session_id

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def id(self) -> str:
        return str(self.user.id)


def get_current_staff(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> StaffContext:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = decode_staff_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token required")

    user = db.get(PlatformUser, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")

    policy = db.get(SecuritySettings, 1)
    settings = get_settings()
    idle_minutes = (
        policy.idle_timeout_minutes
        if policy and policy.idle_timeout_minutes
        else settings.staff_idle_timeout_minutes
    )

    session_id = None
    jti = payload.get("jti") or payload.get("sid")
    if jti:
        session = db.execute(
            select(StaffSession).where(StaffSession.jti == jti)
        ).scalar_one_or_none()
        if session is not None:
            if session.revoked_at is not None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
            now = datetime.now(timezone.utc)
            exp = session.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < now:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

            last_seen = session.last_seen_at
            if last_seen is not None and idle_minutes:
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                if now - last_seen > timedelta(minutes=idle_minutes):
                    session.revoked_at = now
                    session.revoke_reason = "idle_timeout"
                    db.commit()
                    raise HTTPException(
                        status.HTTP_401_UNAUTHORIZED,
                        f"Session idle for more than {idle_minutes} minutes",
                    )

            # Throttle the write so a busy console does not commit on every call.
            if last_seen is None or (now - last_seen) > timedelta(seconds=60):
                session.last_seen_at = now
                db.commit()
            session_id = session.id

    # Permissions are re-derived from the stored role on every request. Reading
    # them from the JWT claim would leave a demoted account with its old
    # privileges until the access token expired.
    permissions = permissions_for_role(user.role)

    if policy and policy.mfa_required and not user.mfa_enabled:
        path = request.url.path
        if not any(path.startswith(exempt) for exempt in _MFA_EXEMPT_PATHS):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "MFA enrolment is required by platform policy. "
                "Complete POST /v1/auth/mfa/setup then /v1/auth/mfa/verify.",
            )

    return StaffContext(
        user=user,
        permissions=permissions,
        session_id=session_id,
    )


def require_permissions(*needed: str):
    def dependency(staff: StaffContext = Depends(get_current_staff)) -> StaffContext:
        missing = [p for p in needed if p not in staff.permissions]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Missing permissions: {', '.join(missing)}",
            )
        return staff

    return dependency


def require_mfa_if_policy(
    staff: StaffContext = Depends(get_current_staff),
    db: Session = Depends(get_db),
) -> StaffContext:
    """Block staff mutations when org MFA policy is on and user has not enrolled."""
    policy = db.get(SecuritySettings, 1)
    if policy and policy.mfa_required and not staff.user.mfa_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "MFA enrollment required by platform policy. Complete /v1/auth/mfa/setup.",
        )
    return staff


class ServiceContext:
    def __init__(self, client: ServiceClient, scopes: list[str]):
        self.client = client
        self.scopes = scopes


def get_service_client(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> ServiceContext:
    """AuthN for m2m routes (/internal/v1). Staff tokens are rejected here."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = decode_m2m_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired m2m token")

    client = db.execute(
        select(ServiceClient).where(ServiceClient.client_id == payload["sub"])
    ).scalar_one_or_none()
    if client is None or not client.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Service client disabled")

    # Throttled: the school server polls runtime config continuously, and writing
    # last_used_at on every call meant a database write per poll per tenant.
    now = datetime.now(timezone.utc)
    last_used = client.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=timezone.utc)
    if last_used is None or (now - last_used) > timedelta(seconds=60):
        client.last_used_at = now
        db.commit()

    # The JWT scopes are only a snapshot. Use the database value so a scope
    # removal takes effect immediately instead of remaining usable until token
    # expiry.
    return ServiceContext(client=client, scopes=list(client.scopes or []))


def require_scope(scope: str):
    def dependency(svc: ServiceContext = Depends(get_service_client)) -> ServiceContext:
        if scope not in svc.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing scope: {scope}")
        return svc

    return dependency


def client_ip(request: Request) -> str:
    """Prefer the IP the middleware already resolved, so the spoofing rules live
    in exactly one place."""
    resolved = getattr(request.state, "client_ip", None)
    if resolved:
        return str(resolved)
    from app.middleware_ops import client_ip as resolve_ip

    return resolve_ip(request, get_settings().trusted_proxy_count)


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


_ACCESS_RANK = {"read": 0, "elevated_pii": 1, "write_support": 2}


def require_support_elevation(tenant_id: int, min_level: str = "elevated_pii"):
    """Dependency factory: require active break-glass session for tenant PII reads."""

    def dependency(
        staff: StaffContext = Depends(get_current_staff),
        db: Session = Depends(get_db),
    ) -> SupportSession:
        min_rank = _ACCESS_RANK.get(min_level, 1)
        sessions = db.execute(
            select(SupportSession).where(
                SupportSession.tenant_id == tenant_id,
                SupportSession.staff_email == staff.email,
                SupportSession.is_active.is_(True),
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for session in sessions:
            ends = session.ends_at
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if ends < now:
                continue
            if _ACCESS_RANK.get(session.access_level, 0) >= min_rank:
                return session
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Active break-glass session required for tenant {tenant_id} "
            f"with access_level >= {min_level}",
        )

    return dependency
