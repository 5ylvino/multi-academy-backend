from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from masms.domain.identity.password_policy import validate_password
from masms.domain.identity.roles import Realm
from masms.domain.identity.session import RefreshSession
from masms.domain.identity.user import LoginPolicy, UserAccount, UserStatus
from masms.domain.shared.errors import AccountLocked, AuthenticationFailed, ValidationFailed
from masms.domain.shared.ids import new_id
from masms.domain.shared.value_objects import EmailAddress

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def user(**kw: object) -> UserAccount:
    defaults: dict[str, object] = {
        "realm": Realm.SCHOOL,
        "tenant_id": new_id(),
        "email": EmailAddress("Teacher@School.NG"),
        "full_name": "Ada Obi",
        "password_hash": "x",
        "roles": frozenset({"class_teacher"}),
        "now": NOW,
    }
    defaults.update(kw)
    return UserAccount.create(**defaults)  # type: ignore[arg-type]


def test_email_is_normalised() -> None:
    assert user().email.value == "teacher@school.ng"


def test_school_user_needs_tenant() -> None:
    with pytest.raises(ValueError, match="tenant"):
        user(tenant_id=None)


def test_lockout_after_max_failures_then_unlock() -> None:
    u = user()
    policy = LoginPolicy(max_failed_attempts=3, lockout=timedelta(minutes=15))
    for _ in range(3):
        u.record_failed_sign_in(NOW, policy)
    with pytest.raises(AccountLocked):
        u.ensure_can_sign_in(NOW + timedelta(minutes=1))
    u.ensure_can_sign_in(NOW + timedelta(minutes=16))


def test_pending_user_cannot_sign_in() -> None:
    with pytest.raises(AuthenticationFailed):
        user(status=UserStatus.PENDING).ensure_can_sign_in(NOW)


def test_session_rotation_marks_old_inactive() -> None:
    first = RefreshSession.start(
        user_id=new_id(),
        realm=Realm.SCHOOL,
        tenant_id=new_id(),
        token_hash="a",
        now=NOW,
        ttl=timedelta(days=1),
    )
    second = RefreshSession.start(
        user_id=first.user_id,
        realm=Realm.SCHOOL,
        tenant_id=first.tenant_id,
        token_hash="b",
        now=NOW,
        ttl=timedelta(days=1),
        family_id=first.family_id,
    )
    first.rotate_to(second, NOW)
    assert first.was_rotated
    assert not first.is_active(NOW)
    assert second.is_active(NOW)
    assert second.family_id == first.family_id
    assert not second.is_active(NOW + timedelta(days=2))


@pytest.mark.parametrize("pw", ["short", "password123", "aaaaaaaaaaaa", "teacher-rocks-99"])
def test_weak_passwords_rejected(pw: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_password(pw, email="teacher@school.ng")


def test_strong_password_accepted() -> None:
    validate_password("correct horse battery", email="teacher@school.ng")


def test_uuid7_ids_are_time_ordered() -> None:
    ids = [new_id() for _ in range(100)]
    assert all(i.version == 7 for i in ids)
    assert [i.bytes[:6] for i in ids] == sorted(i.bytes[:6] for i in ids)
