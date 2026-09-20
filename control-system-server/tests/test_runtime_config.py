"""Runtime config / feature resolution for catalog plans."""

from __future__ import annotations

from app.models import FeatureFlag, Plan, PlanEntitlement, Subscription
from app.services.runtime_config import build_runtime_config, invalidate_runtime_config_cache


def test_elite_plan_inherits_new_flags_without_entitlement_rows(session, tenant):
    """Schools on Elite before ai.* flags shipped had no PlanEntitlement rows."""
    plan = Plan(key="elite", name="Elite", monthly_price_minor=0, rank=3)
    session.add(plan)
    session.flush()

    session.add(
        FeatureFlag(
            key="ai.assistant",
            name="AI assistant",
            description="AI assistant",
            default_enabled=False,
            min_plan="elite",
            requires_providers=["ai"],
        )
    )
    session.add(
        Subscription(
            tenant_id=tenant.id,
            type="catalog",
            plan_id=plan.id,
            status="active",
            billing_cycle="monthly",
            is_current=True,
        )
    )
    session.commit()
    invalidate_runtime_config_cache(tenant.id)

    payload = build_runtime_config(session, tenant)

    assert payload["features"]["ai.assistant"] is True


def test_premium_plan_does_not_inherit_elite_only_flags(session, tenant):
    elite_plan = Plan(key="elite", name="Elite", monthly_price_minor=0, rank=3)
    premium_plan = Plan(key="premium", name="Premium", monthly_price_minor=0, rank=2)
    session.add_all([elite_plan, premium_plan])
    session.flush()

    session.add(
        FeatureFlag(
            key="ai.tutor",
            name="AI tutor",
            description="AI tutor",
            default_enabled=False,
            min_plan="elite",
            requires_providers=["ai"],
        )
    )
    session.add(
        PlanEntitlement(
            plan_id=premium_plan.id, feature_key="fees.gateway", enabled=True
        )
    )
    session.add(
        Subscription(
            tenant_id=tenant.id,
            type="catalog",
            plan_id=premium_plan.id,
            status="active",
            billing_cycle="monthly",
            is_current=True,
        )
    )
    session.commit()
    invalidate_runtime_config_cache(tenant.id)

    payload = build_runtime_config(session, tenant)

    assert payload["features"]["ai.tutor"] is False


def test_explicit_plan_entitlement_can_disable_elite_flag(session, tenant):
    plan = Plan(key="elite", name="Elite", monthly_price_minor=0, rank=3)
    session.add(plan)
    session.flush()

    session.add(
        FeatureFlag(
            key="ai.essay_grading",
            name="Essay grading",
            description="Essay grading",
            default_enabled=False,
            min_plan="elite",
            requires_providers=["ai"],
        )
    )
    session.add(
        PlanEntitlement(
            plan_id=plan.id, feature_key="ai.essay_grading", enabled=False
        )
    )
    session.add(
        Subscription(
            tenant_id=tenant.id,
            type="catalog",
            plan_id=plan.id,
            status="active",
            billing_cycle="monthly",
            is_current=True,
        )
    )
    session.commit()
    invalidate_runtime_config_cache(tenant.id)

    payload = build_runtime_config(session, tenant)

    assert payload["features"]["ai.essay_grading"] is False
