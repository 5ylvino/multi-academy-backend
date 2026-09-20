"""Resolve TenantRuntimeConfig (ControlConfig v1) for the school Nest server.

Precedence per MULTI-ACADEMY-SMS-BUILD.md §2.1 / CONTROL-SYSTEM-BUILD.md §6:
  kill switch (off) > tenant override > plan/custom entitlement > global
  override > catalog default. Capability freezes force keys off regardless.
  Tenant status (blacklisted/suspended/restricted) short-circuits in Nest.
"""

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import current_config_version
from app.catalog import PLAN_RANKS
from app.config import get_settings
from app.models import (
    Block,
    FeatureFlag,
    FlagCohortRollout,
    FlagOverride,
    MaintenanceWindow,
    Plan,
    PlanEntitlement,
    ProviderConfig,
    ProviderFailoverPolicy,
    FeeSplitRule,
    PayrollAccount,
    Subscription,
    Tenant,
    TenantDomain,
    UsageMeter,
    UsageRollup,
)
from app.services.trials import trial_metadata

settings = get_settings()

# Process-local caches for the hot m2m runtime-config endpoint. Invalidated on
# bump_config_version so schools never see stale entitlements after a change.
_RUNTIME_CACHE_TTL = float(settings.runtime_config_ttl_seconds)
_runtime_cache: dict[int, tuple[float, int, dict]] = {}
_runtime_lock = threading.Lock()
_version_cache: tuple[float, int] | None = None
_VERSION_CACHE_TTL = 3.0


@dataclass(frozen=True)
class _CatalogSnapshot:
    """Global catalog rows shared across tenants — loaded once per config version."""

    version: int
    flags: tuple[FeatureFlag, ...]
    rollouts: tuple[FlagCohortRollout, ...]
    deprecated: tuple[FeatureFlag, ...]
    meters: tuple[UsageMeter, ...]
    maintenance_windows: tuple[MaintenanceWindow, ...]
    provider_configs: tuple[ProviderConfig, ...]
    failover_policies: tuple[ProviderFailoverPolicy, ...]


_catalog_snapshot: _CatalogSnapshot | None = None


def invalidate_runtime_config_cache(tenant_id: int | None = None) -> None:
    global _catalog_snapshot, _version_cache
    with _runtime_lock:
        _catalog_snapshot = None
        _version_cache = None
        if tenant_id is None:
            _runtime_cache.clear()
        else:
            _runtime_cache.pop(tenant_id, None)


def _cached_config_version(db: Session) -> int:
    """Avoid a Neon round-trip on every runtime-config request when version is stable."""
    global _version_cache
    now = time.monotonic()
    with _runtime_lock:
        if _version_cache is not None and now - _version_cache[0] < _VERSION_CACHE_TTL:
            return _version_cache[1]
    version = current_config_version(db)
    with _runtime_lock:
        _version_cache = (now, version)
    return version


def _get_catalog_snapshot(db: Session, version: int) -> _CatalogSnapshot:
    global _catalog_snapshot
    with _runtime_lock:
        if _catalog_snapshot is not None and _catalog_snapshot.version == version:
            return _catalog_snapshot

    snapshot = _CatalogSnapshot(
        version=version,
        flags=tuple(db.execute(select(FeatureFlag)).scalars().all()),
        rollouts=tuple(
            db.execute(
                select(FlagCohortRollout).where(FlagCohortRollout.enabled.is_(True))
            ).scalars().all()
        ),
        deprecated=tuple(
            db.execute(select(FeatureFlag).where(FeatureFlag.status == "deprecated")).scalars().all()
        ),
        meters=tuple(db.execute(select(UsageMeter)).scalars().all()),
        maintenance_windows=tuple(
            db.execute(select(MaintenanceWindow).where(MaintenanceWindow.is_active.is_(True))).scalars().all()
        ),
        provider_configs=tuple(
            db.execute(
                select(ProviderConfig).where(ProviderConfig.is_enabled.is_(True))
            ).scalars().all()
        ),
        failover_policies=tuple(
            db.execute(
                select(ProviderFailoverPolicy).where(ProviderFailoverPolicy.enabled.is_(True))
            ).scalars().all()
        ),
    )
    with _runtime_lock:
        if _catalog_snapshot is None or _catalog_snapshot.version != version:
            _catalog_snapshot = snapshot
    return snapshot


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active(block: Block) -> bool:
    if not block.is_active:
        return False
    if block.expires_at is not None:
        expires = block.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _now():
            return False
    return True


def resolve_features(
    db: Session,
    tenant: Tenant,
    catalog: _CatalogSnapshot | None = None,
) -> tuple[dict[str, bool], dict[str, int], list[str]]:
    """Returns (features, quotas, kill_switches)."""
    flags: list[FeatureFlag] = list(catalog.flags) if catalog is not None else db.execute(select(FeatureFlag)).scalars().all()
    overrides: list[FlagOverride] = db.execute(
        select(FlagOverride).where(
            (FlagOverride.tenant_id == tenant.id) | (FlagOverride.tenant_id.is_(None))
        )
    ).scalars().all()

    subscription: Subscription | None = db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True))
        .order_by(Subscription.id.desc())
    ).scalars().first()

    plan: Plan | None = None
    plan_entitlements: dict[str, PlanEntitlement] = {}
    custom_entitlements: dict[str, bool] = {}
    quotas: dict[str, int] = {}

    if subscription is not None:
        if (
            subscription.type == "catalog"
            and subscription.plan_id
            and subscription.status in ("active", "trialing")
        ):
            plan = db.get(Plan, subscription.plan_id)
            if plan is not None:
                rows = db.execute(
                    select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
                ).scalars().all()
                plan_entitlements = {e.feature_key: e for e in rows}
                quotas = {
                    e.feature_key: e.quota for e in rows if e.quota is not None
                }
        elif subscription.type == "custom":
            if subscription.status in ("active", "trialing"):
                custom_entitlements = subscription.custom_entitlements or {}
                quotas = dict(subscription.custom_quotas or {})

    global_overrides = {o.feature_key: o for o in overrides if o.scope == "global"}
    plan_overrides = {
        o.feature_key: o
        for o in overrides
        if o.scope == "plan" and plan is not None and o.plan_id == plan.id
    }
    tenant_overrides = {
        o.feature_key: o for o in overrides if o.scope == "tenant" and o.tenant_id == tenant.id
    }

    plan_rank = PLAN_RANKS.get(plan.key, 0) if plan else 0

    features: dict[str, bool] = {}
    kill_switches: list[str] = []

    for flag in flags:
        if flag.kill_switch:
            features[flag.key] = False
            kill_switches.append(flag.key)
            continue

        enabled = flag.default_enabled

        if flag.key in global_overrides:
            enabled = global_overrides[flag.key].enabled
        if flag.key in plan_overrides:
            enabled = plan_overrides[flag.key].enabled

        # Plan / custom-deal entitlement
        if subscription is not None:
            if subscription.type == "custom":
                if flag.key in custom_entitlements:
                    enabled = bool(custom_entitlements[flag.key])
                elif flag.min_plan:
                    enabled = False  # custom deals opt in to gated features explicitly
            else:
                if flag.key in plan_entitlements:
                    enabled = plan_entitlements[flag.key].enabled
                elif flag.min_plan:
                    # Catalog plans inherit every flag their rank covers even when
                    # PlanEntitlement rows were not backfilled after a catalog update.
                    required = PLAN_RANKS.get(flag.min_plan, 99)
                    enabled = plan_rank >= required
        elif flag.min_plan:
            # No subscription on record → plan-gated features stay off.
            enabled = False

        # Tenant override wins over plan/global
        if flag.key in tenant_overrides:
            enabled = tenant_overrides[flag.key].enabled

        features[flag.key] = enabled

    # Cohort / percentage rollouts: when a rollout exists and is enabled,
    # the feature is ON only if tenant matches cohort tags OR falls in % bucket.
    # Applied after base resolution — does not override kill switches or explicit tenant off.
    rollouts: list[FlagCohortRollout] = (
        list(catalog.rollouts)
        if catalog is not None
        else db.execute(
            select(FlagCohortRollout).where(FlagCohortRollout.enabled.is_(True))
        ).scalars().all()
    )
    tenant_tags = set(tenant.tags or [])
    bucket = int(hashlib.sha256(tenant.external_id.encode()).hexdigest()[:8], 16) % 100
    for rollout in rollouts:
        if rollout.feature_key not in features:
            continue
        if rollout.feature_key in kill_switches:
            continue
        # Explicit tenant override already applied — skip further gating.
        if rollout.feature_key in tenant_overrides:
            continue
        tag_hit = bool(tenant_tags.intersection(set(rollout.cohort_tags or [])))
        pct_hit = bucket < int(rollout.percentage or 0)
        if tag_hit or pct_hit:
            features[rollout.feature_key] = True
        else:
            # Rollout present means staged enable — keep off unless already on via plan and no rollout gate.
            # Spec: percentage/cohort rollout controls progressive enablement of features that are off by default.
            if not features[rollout.feature_key]:
                features[rollout.feature_key] = False

    return features, quotas, kill_switches


def _failover_active(policy: ProviderFailoverPolicy) -> bool:
    if not policy.enabled or not policy.is_tripped:
        return False
    if policy.tripped_at is None:
        return True
    tripped = policy.tripped_at
    if tripped.tzinfo is None:
        tripped = tripped.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > tripped + timedelta(seconds=policy.cooldown_seconds):
        return False
    return True


def resolve_providers(
    db: Session,
    tenant: Tenant,
    catalog: _CatalogSnapshot | None = None,
) -> dict:
    if catalog is not None:
        rows = [
            row
            for row in catalog.provider_configs
            if row.tenant_id is None or row.tenant_id == tenant.id
        ]
        policies = [
            policy
            for policy in catalog.failover_policies
            if policy.tenant_id is None or policy.tenant_id == tenant.id
        ]
    else:
        rows = db.execute(
            select(ProviderConfig).where(
                ProviderConfig.is_enabled.is_(True),
                (ProviderConfig.tenant_id == tenant.id) | (ProviderConfig.tenant_id.is_(None)),
            )
        ).scalars().all()
        policies = db.execute(
            select(ProviderFailoverPolicy).where(
                ProviderFailoverPolicy.enabled.is_(True),
                (ProviderFailoverPolicy.tenant_id == tenant.id)
                | (ProviderFailoverPolicy.tenant_id.is_(None)),
            )
        ).scalars().all()

    chosen: dict[str, ProviderConfig] = {}
    for row in rows:
        existing = chosen.get(row.capability)
        # Tenant-specific rows win over global defaults.
        if existing is None or (existing.tenant_id is None and row.tenant_id is not None):
            chosen[row.capability] = row
    policy_by_cap: dict[str, ProviderFailoverPolicy] = {}
    for p in policies:
        existing = policy_by_cap.get(p.capability)
        if existing is None or (existing.tenant_id is None and p.tenant_id is not None):
            policy_by_cap[p.capability] = p

    providers: dict[str, dict] = {}
    for capability, cfg in chosen.items():
        provider_id = cfg.provider_id
        failover_meta = None
        policy = policy_by_cap.get(capability)
        if policy and _failover_active(policy):
            # Primary unhealthy / tripped → secondary
            if cfg.provider_id == policy.primary_provider_id or policy.is_tripped:
                provider_id = policy.secondary_provider_id
                failover_meta = {
                    "failover": True,
                    "primaryProviderId": policy.primary_provider_id,
                    "secondaryProviderId": policy.secondary_provider_id,
                }
        entry: dict = {"providerId": provider_id, "mode": cfg.mode}
        # Only whitelisted non-secret settings are exposed to the school server.
        entry.update(cfg.settings or {})
        if failover_meta:
            entry.update(failover_meta)
        # Also trip when configured primary health is red
        if (
            policy
            and policy.enabled
            and cfg.provider_id == policy.primary_provider_id
            and cfg.health_status == "red"
        ):
            entry["providerId"] = policy.secondary_provider_id
            entry["failover"] = True
            entry["primaryProviderId"] = policy.primary_provider_id
            entry["secondaryProviderId"] = policy.secondary_provider_id
        providers[capability] = entry
    return providers


def resolve_enforcement(db: Session, tenant: Tenant) -> dict:
    blocks: list[Block] = db.execute(
        select(Block).where(
            Block.is_active.is_(True),
            (Block.tenant_id == tenant.id) | (Block.tenant_id.is_(None)),
        )
    ).scalars().all()
    blocks = [b for b in blocks if _active(b)]
    trial = trial_metadata(db, tenant)
    trial_expired = bool(trial and trial["expired"])

    return {
        "schoolBlacklisted": tenant.status == "blacklisted",
        "blockedUserIds": [b.value for b in blocks if b.target_type == "user"],
        "blockedEmails": [b.value for b in blocks if b.target_type == "email"],
        "blockedIpCidrs": [b.value for b in blocks if b.target_type == "ip"],
        "blockedDevices": [b.value for b in blocks if b.target_type == "device"],
        "capabilityFreezes": [b.value for b in blocks if b.target_type == "capability"],
        "denyLogin": tenant.status in (
            "pending_verification",
            "blacklisted",
            "suspended",
            "restricted",
            "provisioning",
            "archived",
        ) or trial_expired,
        "trialExpired": trial_expired,
        "message": tenant.status_message or None,
    }


def resolve_maintenance(
    db: Session,
    tenant: Tenant,
    catalog: _CatalogSnapshot | None = None,
) -> dict | None:
    now = _now()
    windows: list[MaintenanceWindow] = (
        list(catalog.maintenance_windows)
        if catalog is not None
        else db.execute(
            select(MaintenanceWindow).where(MaintenanceWindow.is_active.is_(True))
        ).scalars().all()
    )
    for w in windows:
        starts, ends = w.starts_at, w.ends_at
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        if starts <= now <= ends and (not w.tenant_ids or tenant.id in w.tenant_ids):
            return {"active": True, "message": w.message, "until": ends.isoformat()}
    return None


def build_runtime_config(db: Session, tenant: Tenant) -> dict:
    now_mono = time.monotonic()
    with _runtime_lock:
        cached = _runtime_cache.get(tenant.id)
        if cached is not None and now_mono - cached[0] < _RUNTIME_CACHE_TTL:
            return cached[2]

    version = _cached_config_version(db)
    with _runtime_lock:
        cached = _runtime_cache.get(tenant.id)
        if (
            cached is not None
            and cached[1] == version
            and now_mono - cached[0] < _RUNTIME_CACHE_TTL
        ):
            return cached[2]

    payload = _build_runtime_config_uncached(db, tenant, version)
    with _runtime_lock:
        _runtime_cache[tenant.id] = (now_mono, version, payload)
        # Bound memory: drop oldest entries if the map grows large.
        if len(_runtime_cache) > 2000:
            for key in list(_runtime_cache)[:500]:
                _runtime_cache.pop(key, None)
    return payload


def _build_runtime_config_uncached(db: Session, tenant: Tenant, version: int) -> dict:
    catalog = _get_catalog_snapshot(db, version)
    features, quotas, kill_switches = resolve_features(db, tenant, catalog)
    enforcement = resolve_enforcement(db, tenant)
    trial = trial_metadata(db, tenant)

    # Capability freezes force features off even when the flag resolves on.
    for frozen_key in enforcement["capabilityFreezes"]:
        for key in list(features):
            if key == frozen_key or key.startswith(frozen_key.rstrip("*")) and frozen_key.endswith("*"):
                features[key] = False

    subscription: Subscription | None = db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True))
        .order_by(Subscription.id.desc())
    ).scalars().first()

    sub_payload = None
    if subscription is not None:
        plan = db.get(Plan, subscription.plan_id) if subscription.plan_id else None
        sub_payload = {
            "type": subscription.type,
            "planId": plan.key if plan else None,
            "dealId": str(subscription.id) if subscription.type == "custom" else None,
            "billingCycle": subscription.billing_cycle,
            "status": subscription.status,
            "trial": trial,
        }

    domains = db.execute(
        select(TenantDomain)
        .where(
            TenantDomain.tenant_id == tenant.id,
            TenantDomain.status == "active",
        )
        .order_by(TenantDomain.is_primary.desc(), TenantDomain.id)
    ).scalars().all()
    fee_splits = db.execute(
        select(FeeSplitRule).where(
            FeeSplitRule.tenant_id == tenant.id,
            FeeSplitRule.is_active.is_(True),
        )
    ).scalars().all()
    payroll_accounts = db.execute(
        select(PayrollAccount).where(
            PayrollAccount.tenant_id == tenant.id,
            PayrollAccount.is_active.is_(True),
        )
    ).scalars().all()

    from app.services.payment_settings import payment_settings_payload
    from app.services.tutoring_policy import tutoring_policy_payload

    payment_settings = payment_settings_payload(db, tenant, features)
    tutoring_policy = tutoring_policy_payload(db, tenant.id)

    # Usage remaining vs quotas (Phase B) — soft signal for Nest FinOps.
    usage_remaining: dict[str, int] = {}
    try:
        from app.services.usage_metering import period_key

        pk = period_key()
        meters = {m.key: m for m in catalog.meters}
        rollups = {
            r.meter_key: r.quantity
            for r in db.execute(
                select(UsageRollup).where(
                    UsageRollup.tenant_id == tenant.id,
                    UsageRollup.period_key == pk,
                )
            ).scalars().all()
        }
        for meter_key, used in rollups.items():
            meter = meters.get(meter_key)
            feature_key = meter.feature_key if meter else meter_key
            cap = quotas.get(feature_key)
            if cap is not None:
                usage_remaining[feature_key] = max(0, int(cap) - int(used))
    except Exception:
        usage_remaining = {}

    deprecated = [
        {
            "key": f.key,
            "removalDate": f.removal_date.isoformat() if f.removal_date else None,
            "note": f.deprecation_note or "",
        }
        for f in catalog.deprecated
    ]

    return {
        "tenantId": tenant.external_id,
        "slug": tenant.slug,
        "domains": [
            {
                "hostname": domain.hostname,
                "kind": domain.kind,
                "isPrimary": domain.is_primary,
                "sslStatus": domain.ssl_status,
            }
            for domain in domains
        ],
        "feeSplits": [
            {
                "feeType": rule.fee_type,
                "providerId": rule.provider_id,
                "currency": rule.currency,
                "allocations": rule.allocations,
            }
            for rule in fee_splits
        ],
        "payroll": {
            "accounts": [
                {
                    "role": account.account_role,
                    "providerId": account.provider_id,
                    "accountReference": account.account_reference,
                    "currency": account.currency,
                }
                for account in payroll_accounts
            ],
            "salaryAccountSeparated": any(
                account.account_role == "salary" for account in payroll_accounts
            )
            and any(account.account_role == "operating" for account in payroll_accounts),
        },
        "paymentSettings": payment_settings,
        "tutoring": tutoring_policy,
        "subscription": sub_payload,
        "trial": trial,
        "status": tenant.status,
        "residencyTag": getattr(tenant, "residency_tag", None) or tenant.region,
        "legalHold": bool(getattr(tenant, "legal_hold", False)),
        "features": features,
        "quotas": quotas,
        "usageRemaining": usage_remaining,
        "providers": resolve_providers(db, tenant, catalog),
        "enforcement": enforcement,
        "maintenance": resolve_maintenance(db, tenant, catalog),
        "killSwitches": kill_switches,
        "deprecatedFeatures": deprecated,
        "ttlSeconds": settings.runtime_config_ttl_seconds,
        "updatedAt": _now().isoformat(),
        "configVersion": version,
    }
