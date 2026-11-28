from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.shared.errors import InvalidStateTransition, TenantUnavailable, ValidationFailed
from app.domain.shared.value_objects import Slug
from app.domain.tenancy.events import TenantRegistered, TenantStatusChanged
from app.domain.tenancy.tenant import SchoolLevel, Tenant, TenantStatus

NOW = datetime(2026, 11, 27, tzinfo=UTC)


def make_tenant() -> Tenant:
    return Tenant.register(
        name="  Greenfield   Academy ",
        slug=Slug("greenfield"),
        school_levels=frozenset({SchoolLevel.PRIMARY}),
        country_code="ng",
        contact_email="head@greenfield.ng",
        plan_code="starter",
        now=NOW,
    )


def test_register_normalises_and_records_event() -> None:
    tenant = make_tenant()
    assert tenant.name == "Greenfield Academy"
    assert tenant.country_code == "NG"
    assert tenant.status is TenantStatus.PENDING_VERIFICATION
    assert [type(e) for e in tenant.pull_events()] == [TenantRegistered]


def test_pending_school_is_not_serving() -> None:
    with pytest.raises(TenantUnavailable):
        make_tenant().ensure_serving()


def test_trial_then_suspend_then_reinstate() -> None:
    tenant = make_tenant()
    tenant.start_trial(NOW, timedelta(days=30))
    assert tenant.is_serving
    version = tenant.config_version
    tenant.suspend("Unpaid invoice")
    assert tenant.status is TenantStatus.SUSPENDED
    assert tenant.config_version == version + 1
    tenant.reinstate(NOW + timedelta(days=1))
    assert tenant.status is TenantStatus.TRIAL
    tenant.suspend("again")
    tenant.reinstate(NOW + timedelta(days=60))
    assert tenant.status is TenantStatus.ACTIVE
    changes = [e for e in tenant.pull_events() if isinstance(e, TenantStatusChanged)]
    assert len(changes) == 5


def test_suspend_needs_a_reason() -> None:
    tenant = make_tenant()
    tenant.start_trial(NOW, timedelta(days=30))
    with pytest.raises(ValidationFailed):
        tenant.suspend("   ")


def test_offboarded_is_terminal() -> None:
    tenant = make_tenant()
    tenant.transition(TenantStatus.OFFBOARDED)
    with pytest.raises(InvalidStateTransition):
        tenant.activate()


def test_pending_cannot_jump_to_active() -> None:
    with pytest.raises(InvalidStateTransition):
        make_tenant().activate()


@pytest.mark.parametrize("bad", ["ab", "-bad", "Has Space", "admin", "x" * 64])
def test_slug_rules(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        Slug(bad)


def test_slug_from_name() -> None:
    assert Slug.from_name("St. Mary's College, Lagos!").value == "st-mary-s-college-lagos"
