from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from masms.domain.platform.feature_flags import FeatureFlag, FlagContext, Plan, resolve_flag
from masms.domain.shared.errors import ValidationFailed
from masms.domain.shared.ids import new_id

NOW = datetime(2026, 11, 27, tzinfo=UTC)
TENANT = UUID("01937000-0000-7000-8000-000000000001")


def flag(**kw: object) -> FeatureFlag:
    return FeatureFlag(key="fees.installments", name="Installments", updated_at=NOW, **kw)  # type: ignore[arg-type]


def ctx(*, plan: Plan | None = None, overrides: dict[str, bool] | None = None) -> FlagContext:
    return FlagContext(tenant_id=TENANT, plan=plan, overrides=overrides or {})


def test_unknown_flag_is_off() -> None:
    assert resolve_flag(None, ctx()) is False


def test_kill_switch_beats_override() -> None:
    assert resolve_flag(flag(kill_switch=True), ctx(overrides={"fees.installments": True})) is False


def test_override_beats_plan_and_default() -> None:
    plan = Plan(code="premium", name="Premium", features=frozenset({"fees.installments"}))
    assert (
        resolve_flag(
            flag(default_enabled=True), ctx(plan=plan, overrides={"fees.installments": False})
        )
        is False
    )


def test_plan_enables_feature() -> None:
    plan = Plan(code="premium", name="Premium", features=frozenset({"fees.installments"}))
    assert resolve_flag(flag(), ctx(plan=plan)) is True


def test_default_applies_last() -> None:
    assert resolve_flag(flag(default_enabled=True), ctx()) is True
    assert resolve_flag(flag(), ctx()) is False


def test_rollout_is_stable_and_roughly_proportional() -> None:
    f = flag(rollout_percent=30)
    tenants = [new_id() for _ in range(4000)]
    first = [f.in_rollout(t) for t in tenants]
    assert first == [f.in_rollout(t) for t in tenants]
    share = sum(first) / len(first)
    assert 0.25 < share < 0.35


@pytest.mark.parametrize("key", ["Fees", "fees..x", "1fees", "fees-installments"])
def test_bad_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValidationFailed):
        FeatureFlag(key=key, name="x", updated_at=NOW)
