"""Idempotent seeding: flag catalog, catalog plans + entitlements, global
default providers, bootstrap owner, and system state row."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import (
    DEFAULT_PROVIDERS,
    ELITE_EXTRAS,
    FLAG_CATALOG,
    PLAN_RANKS,
    PREMIUM_EXTRAS,
)
from app.config import get_settings
from app.models import (
    FeatureFlag,
    Plan,
    PlanEntitlement,
    PlatformUser,
    ProviderConfig,
    SecuritySettings,
    ServiceClient,
    StatusComponent,
    Subprocessor,
    SystemState,
)
from app.security import hash_client_secret, hash_password

settings = get_settings()

PLAN_DEFS = [
    ("basic", "Basic", 0),
    ("premium", "Premium", 0),
    ("elite", "Elite", 0),
]


def seed_flags(db: Session) -> int:
    existing_flags = {f.key: f for f in db.execute(select(FeatureFlag)).scalars()}
    created = 0
    for key, name, default_enabled, min_plan, requires in FLAG_CATALOG:
        existing = existing_flags.get(key)
        if existing is not None:
            # Keep existing databases aligned when catalog defaults or plan
            # requirements change.
            existing.name = name
            existing.description = name
            existing.default_enabled = default_enabled
            existing.min_plan = min_plan
            existing.requires_providers = requires
            continue
        db.add(
            FeatureFlag(
                key=key,
                name=name,
                description=name,
                default_enabled=default_enabled,
                min_plan=min_plan,
                requires_providers=requires,
            )
        )
        created += 1
    return created


def seed_plans(db: Session) -> int:
    plans = {p.key: p for p in db.execute(select(Plan)).scalars()}
    for key, name, price in PLAN_DEFS:
        if key not in plans:
            plan = Plan(
                key=key, name=name, monthly_price_minor=price, rank=PLAN_RANKS[key]
            )
            db.add(plan)
            db.flush()
            plans[key] = plan

    default_on = [key for key, _, on, _, _ in FLAG_CATALOG if on]
    plan_features = {
        "basic": default_on,
        "premium": default_on + PREMIUM_EXTRAS,
        "elite": default_on + ELITE_EXTRAS,
    }
    added = 0
    for plan_key, feature_keys in plan_features.items():
        plan = plans[plan_key]
        existing = {
            e.feature_key
            for e in db.execute(
                select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id)
            ).scalars()
        }
        for feature_key in feature_keys:
            if feature_key not in existing:
                db.add(
                    PlanEntitlement(
                        plan_id=plan.id, feature_key=feature_key, enabled=True
                    )
                )
                added += 1
    return added


def seed_providers(db: Session) -> None:
    existing = {
        (c.capability, c.tenant_id)
        for c in db.execute(select(ProviderConfig)).scalars()
    }
    for capability, provider_id in DEFAULT_PROVIDERS.items():
        if (capability, None) not in existing:
            db.add(
                ProviderConfig(
                    capability=capability,
                    tenant_id=None,
                    provider_id=provider_id,
                    mode="sandbox",
                    settings={},
                    is_enabled=True,
                )
            )


def seed_bootstrap_owner(db: Session) -> tuple[bool, bool]:
    """Create or synchronize the owner configured by the environment.

    The original implementation only created this account when the database
    was empty, which left old bootstrap credentials active after `.env`
    changed. The first owner is the bootstrap account, so keep its identity
    and password synchronized with the current environment values.
    """
    configured_email = settings.bootstrap_owner_email.strip().lower()
    if not configured_email or not settings.bootstrap_owner_password:
        return False, False

    user = db.execute(
        select(PlatformUser).where(PlatformUser.email == configured_email)
    ).scalar_one_or_none()

    if user is None:
        user = db.execute(
            select(PlatformUser)
            .where(PlatformUser.role == "owner")
            .order_by(PlatformUser.id)
        ).scalars().first()

    if user is None:
        any_user = db.execute(select(PlatformUser)).scalars().first()
        if any_user is not None:
            return False, False
        db.add(
            PlatformUser(
                email=configured_email,
                full_name="Platform Owner",
                password_hash=hash_password(settings.bootstrap_owner_password),
                role="owner",
                is_active=True,
            )
        )
        return True, False

    # Bootstrap credentials are first-boot only. Never reset an existing
    # owner's password or lockout state during a restart.
    return False, False


def seed_bootstrap_m2m(db: Session) -> bool:
    """Ensure the local Nest m2m client exists (idempotent by client_id)."""
    client_id = settings.bootstrap_m2m_client_id
    if not client_id:
        return False
    existing = db.execute(
        select(ServiceClient).where(ServiceClient.client_id == client_id)
    ).scalar_one_or_none()
    if existing is not None:
        scopes = list(existing.scopes or [])
        required_scopes = [
            "provider-secrets:read",
            "plans:read",
            "subscriptions:read",
            "subscriptions:write",
            "billing:read",
            "billing:write",
        ]
        missing_scopes = [scope for scope in required_scopes if scope not in scopes]
        if missing_scopes:
            existing.scopes = [*scopes, *missing_scopes]
        return False
    db.add(
        ServiceClient(
            client_id=client_id,
            name=settings.bootstrap_m2m_name,
            secret_hash=hash_client_secret(settings.bootstrap_m2m_client_secret),
            scopes=[
                "runtime-config:read",
                "provider-secrets:read",
                "plans:read",
                "subscriptions:read",
                "subscriptions:write",
                "billing:read",
                "billing:write",
                "usage:write",
                "health:write",
                "abuse:write",
            ],
            environment=settings.environment,
            is_active=True,
        )
    )
    return True


def seed_system_state(db: Session) -> None:
    if db.execute(select(SystemState)).scalars().first() is None:
        db.add(SystemState(config_version=1))
        db.flush()


def migrate_phase_b_columns(db: Session) -> None:
    """Best-effort ALTER when create_all won't add columns to existing tables.

    Compares SQLAlchemy metadata to the live schema and issues ADD COLUMN for
    any missing fields (SQLite + Postgres). Also keeps an explicit fallback list
    for older DBs when inspector access fails.
    """
    from sqlalchemy import inspect as sa_inspect, text

    from app.database import Base, engine

    # Explicit fallbacks (kept for clarity / older installs)
    alters = [
        "ALTER TABLE subscriptions ADD COLUMN dunning_step VARCHAR(32) DEFAULT 'none'",
        "ALTER TABLE subscriptions ADD COLUMN past_due_at TIMESTAMP",
        "ALTER TABLE subscriptions ADD COLUMN grace_until TIMESTAMP",
        "ALTER TABLE subscriptions ADD COLUMN deal_name VARCHAR(255) DEFAULT ''",
        "ALTER TABLE subscriptions ADD COLUMN approval_status VARCHAR(32) DEFAULT 'active'",
        "ALTER TABLE subscriptions ADD COLUMN next_invoice_at TIMESTAMP",
        "ALTER TABLE subscriptions ADD COLUMN billing_schedule JSON",
        "ALTER TABLE subscriptions ADD COLUMN pricing_model VARCHAR(32) DEFAULT 'flat'",
        "ALTER TABLE subscriptions ADD COLUMN per_student_minor INTEGER",
        "ALTER TABLE subscriptions ADD COLUMN student_count INTEGER",
        "ALTER TABLE deal_templates ADD COLUMN pricing_model VARCHAR(32) DEFAULT 'flat'",
        "ALTER TABLE deal_templates ADD COLUMN per_student_minor INTEGER",
        "ALTER TABLE deal_templates ADD COLUMN student_count INTEGER",
        "ALTER TABLE tenants ADD COLUMN residency_tag VARCHAR(32) DEFAULT 'NG'",
        "ALTER TABLE tenants ADD COLUMN legal_hold BOOLEAN DEFAULT 0",
        "ALTER TABLE tenants ADD COLUMN legal_hold_reason TEXT DEFAULT ''",
        "ALTER TABLE tenants ADD COLUMN courtesy_unlock_until TIMESTAMP",
        "ALTER TABLE feature_flags ADD COLUMN deprecation_note TEXT DEFAULT ''",
        "ALTER TABLE feature_flags ADD COLUMN removal_date TIMESTAMP",
        "ALTER TABLE feature_flags ADD COLUMN deprecated_at TIMESTAMP",
        "ALTER TABLE saas_invoices ADD COLUMN tax_minor INTEGER DEFAULT 0",
        "ALTER TABLE saas_invoices ADD COLUMN tax_label VARCHAR(64) DEFAULT 'VAT'",
        "ALTER TABLE outbox_events ADD COLUMN delivery_log TEXT DEFAULT '[]'",
        "ALTER TABLE provider_configs ADD COLUMN health_status VARCHAR(16) DEFAULT 'unknown'",
    ]

    # Auto-diff: any mapped column missing on an existing table
    try:
        insp = sa_inspect(engine)
        live_tables = set(insp.get_table_names())
        for table_name, table in Base.metadata.tables.items():
            if table_name not in live_tables:
                continue
            live_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col in table.columns:
                if col.name in live_cols or col.primary_key:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default_sql = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    arg = col.default.arg
                    if isinstance(arg, bool):
                        default_sql = f" DEFAULT {1 if arg else 0}"
                    elif isinstance(arg, (int, float)):
                        default_sql = f" DEFAULT {arg}"
                    elif isinstance(arg, str):
                        default_sql = f" DEFAULT '{arg}'"
                    elif callable(arg):
                        default_sql = ""  # e.g. list/dict factories — leave nullable
                elif col.nullable:
                    nullable = "NULL"
                alters.append(
                    f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{default_sql}"
                )
    except Exception:
        pass

    seen: set[str] = set()
    for sql in alters:
        if sql in seen:
            continue
        seen.add(sql)
        try:
            db.execute(text(sql))
            db.commit()
        except Exception:
            db.rollback()


def seed_security_settings(db: Session) -> bool:
    if db.get(SecuritySettings, 1) is not None:
        return False
    db.add(
        SecuritySettings(
            id=1,
            mfa_required=False,
            ip_allowlist_enforced=settings.ip_allowlist_enforced,
            idle_timeout_minutes=settings.staff_idle_timeout_minutes,
        )
    )
    return True


def seed_status_components(db: Session) -> int:
    defaults = [
        ("control_api", "Control API", "Platform control plane", 10),
        ("school_api", "School API", "Nest tenant runtimes", 20),
        ("payments", "Payments", "Paystack / Flutterwave checkout", 30),
        ("sms", "SMS", "Africa's Talking / SMS providers", 40),
        ("email", "Email", "Transactional email", 50),
        ("meetings", "Meetings", "Google Meet / Zoom", 60),
        ("ai", "AI", "AI assistant providers", 70),
    ]
    existing = {c.key for c in db.execute(select(StatusComponent)).scalars()}
    created = 0
    for key, name, desc, order in defaults:
        if key in existing:
            continue
        db.add(
            StatusComponent(
                key=key,
                name=name,
                description=desc,
                status="operational",
                sort_order=order,
                is_public=True,
            )
        )
        created += 1
    return created


def seed_subprocessors(db: Session) -> int:
    defaults = [
        ("Paystack", "Payment processing", "NG", "https://paystack.com/terms"),
        (
            "Flutterwave",
            "Payment processing (alternate)",
            "NG",
            "https://flutterwave.com",
        ),
        ("Africa's Talking", "SMS delivery", "KE", "https://africastalking.com"),
        ("Resend", "Transactional email", "US", "https://resend.com"),
        ("Google", "Meetings / OAuth", "global", "https://cloud.google.com/terms"),
        ("OpenAI", "AI assistant (optional)", "US", "https://openai.com/policies"),
        (
            "Kilo AI Gateway",
            "AI multi-model gateway incl. NVIDIA models (optional)",
            "US",
            "https://kilo.ai",
        ),
    ]
    existing = {s.name for s in db.execute(select(Subprocessor)).scalars()}
    created = 0
    for name, purpose, region, dpa in defaults:
        if name in existing:
            continue
        db.add(
            Subprocessor(
                name=name, purpose=purpose, region=region, dpa_url=dpa, is_active=True
            )
        )
        created += 1
    return created


def seed_all(db: Session, *, migrate_schema: bool = True) -> dict:
    from app.audit import bump_config_version

    if migrate_schema:
        migrate_phase_b_columns(db)
    flags_created = seed_flags(db)
    entitlements_added = seed_plans(db)
    seed_providers(db)
    status_created = seed_status_components(db)
    sub_created = seed_subprocessors(db)
    owner_created, owner_updated = seed_bootstrap_owner(db)
    m2m_created = seed_bootstrap_m2m(db)
    seed_system_state(db)
    security_created = seed_security_settings(db)
    from app.services.usage_metering import ensure_default_meters

    ensure_default_meters(db)
    if flags_created or entitlements_added:
        bump_config_version(db)
    db.commit()
    return {
        "flagsCreated": flags_created,
        "entitlementsAdded": entitlements_added,
        "ownerCreated": owner_created,
        "ownerUpdated": owner_updated,
        "m2mClientCreated": m2m_created,
        "statusComponentsCreated": status_created,
        "subprocessorsCreated": sub_created,
        "securitySettingsCreated": security_created,
    }
