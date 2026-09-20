from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import PlatformUser
from app.models.platform import PLATFORM_ROLES
from app.rbac import permissions_for_role
from app.schemas import StaffUserCreate, StaffUserOut, StaffUserUpdate
from app.security import hash_password

router = APIRouter(prefix="/v1/platform-users", tags=["platform-users"])


def _out(user: PlatformUser) -> StaffUserOut:
    out = StaffUserOut.model_validate(user)
    out.permissions = permissions_for_role(user.role)
    return out


@router.get("", response_model=list[StaffUserOut])
def list_users(
    _: StaffContext = Depends(require_permissions("platform_users:read")),
    db: Session = Depends(get_db),
):
    users = db.execute(select(PlatformUser).order_by(PlatformUser.id)).scalars().all()
    return [_out(u) for u in users]


@router.post("", response_model=StaffUserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: StaffUserCreate,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    if body.role not in PLATFORM_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {body.role}")
    exists = db.execute(
        select(PlatformUser).where(PlatformUser.email == body.email.lower())
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = PlatformUser(
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    record_audit(
        db,
        action="platform_user.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="platform_user",
        target_id=body.email,
        after={"role": body.role},
    )
    db.commit()
    db.refresh(user)
    return _out(user)


@router.patch("/{user_id}", response_model=StaffUserOut)
def update_user(
    user_id: int,
    body: StaffUserUpdate,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    user = db.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if body.role is not None and body.role not in PLATFORM_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {body.role}")

    before = {"role": user.role, "is_active": user.is_active, "full_name": user.full_name}
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.password_hash = hash_password(body.password)

    record_audit(
        db,
        action="platform_user.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="platform_user",
        target_id=str(user_id),
        before=before,
        after={"role": user.role, "is_active": user.is_active, "full_name": user.full_name},
    )
    db.commit()
    db.refresh(user)
    return _out(user)
