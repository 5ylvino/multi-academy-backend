from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    mfa_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "StaffUserOut"


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


class StaffSessionOut(ORMModel):
    id: int
    user_id: int
    ip_address: str
    user_agent: str
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str


class SecuritySettingsOut(ORMModel):
    mfa_required: bool
    ip_allowlist_enforced: bool
    idle_timeout_minutes: int
    updated_by: str = ""
    updated_at: datetime | None = None


class SecuritySettingsUpdate(BaseModel):
    mfa_required: bool | None = None
    ip_allowlist_enforced: bool | None = None
    idle_timeout_minutes: int | None = Field(default=None, ge=5, le=240)
    reason: str = ""


class IpAllowlistOut(ORMModel):
    id: int
    cidr: str
    label: str
    is_active: bool
    created_by: str
    created_at: datetime


class IpAllowlistCreate(BaseModel):
    cidr: str
    label: str = ""


class StaffUserOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    mfa_enabled: bool
    last_login_at: datetime | None = None
    permissions: list[str] = []


class StaffUserCreate(BaseModel):
    email: EmailStr
    full_name: str = ""
    password: str = Field(min_length=8)
    role: str = "auditor"


class StaffUserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


# ----------------------------------------------------------------- tenants


class TenantOut(ORMModel):
    id: int
    external_id: str
    slug: str
    name: str
    status: str
    status_reason: str = ""
    status_message: str
    courtesy_unlock_until: datetime | None = None
    region: str
    residency_tag: str = "NG"
    isolation_mode: str
    legal_hold: bool = False
    legal_hold_reason: str = ""
    admin_email: str
    admin_phone: str
    tags: list
    notes: str
    created_at: datetime
    trial_status: str | None = None
    trial_ends_at: datetime | None = None
    trial_days_remaining: int | None = None


class TenantDomainOut(ORMModel):
    id: int
    tenant_id: int
    hostname: str
    kind: str
    status: str
    ssl_status: str
    is_primary: bool
    created_at: datetime


class TenantDomainCreate(BaseModel):
    hostname: str = ""
    kind: str = "subdomain"


class TenantCreate(BaseModel):
    external_id: str
    slug: str
    name: str
    region: str = "NG"
    residency_tag: str = "NG"
    admin_email: str = ""
    admin_phone: str = ""
    plan_key: str | None = "basic"


class TenantUpdate(BaseModel):
    name: str | None = None
    region: str | None = None
    residency_tag: str | None = None
    legal_hold: bool | None = None
    legal_hold_reason: str | None = None
    admin_email: str | None = None
    admin_phone: str | None = None
    tags: list | None = None
    notes: str | None = None


class TenantStatusChange(BaseModel):
    status: (
        str  # active | suspended | restricted | blacklisted | provisioning | archived
    )
    reason: str = ""
    message: str = ""  # shown to school users when restricted/suspended


class ProvisioningRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ProvisioningResult(BaseModel):
    status: str
    school_db_status: str
    error: str = ""
    result: dict = {}


class ProvisioningJobOut(ORMModel):
    id: int
    tenant_id: int
    status: str
    school_db_status: str
    idempotency_key: str
    defaults_version: str
    requested_by: str
    error: str
    result: dict
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class BlacklistRequest(BaseModel):
    reason: str
    evidence: str = ""
    severity: str = "high"
    permanent: bool = True
    review_by: datetime | None = None


# --------------------------------------------------------------------- plans


class EntitlementOut(ORMModel):
    feature_key: str
    enabled: bool
    name: str | None = None
    quota: int | None = None


class PlanOut(ORMModel):
    id: int
    key: str
    name: str
    description: str
    monthly_price_minor: int
    currency: str
    rank: int
    is_active: bool
    entitlements: list[EntitlementOut] = []


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    monthly_price_minor: int | None = None
    currency: str | None = None
    is_active: bool | None = None


class EntitlementSet(BaseModel):
    feature_key: str
    enabled: bool = True
    quota: int | None = None


# ------------------------------------------------------------- subscriptions


class SubscriptionOut(ORMModel):
    id: int
    tenant_id: int
    type: str
    plan_id: int | None
    status: str
    billing_cycle: str
    price_minor: int | None
    currency: str
    pricing_model: str = "flat"
    per_student_minor: int | None = None
    student_count: int | None = None
    custom_entitlements: dict | None
    custom_quotas: dict | None
    contract_start: datetime | None
    contract_end: datetime | None
    notes: str
    is_current: bool
    dunning_step: str = "none"
    past_due_at: datetime | None = None
    grace_until: datetime | None = None
    deal_name: str = ""
    approval_status: str = "active"
    next_invoice_at: datetime | None = None
    billing_schedule: dict | None = None
    pending_invoice_id: int | str | None = None


class SubscriptionCreate(BaseModel):
    type: str = "catalog"  # catalog | custom
    plan_key: str | None = None
    billing_cycle: str = "monthly"
    price_minor: int | None = None
    currency: str = "NGN"
    pricing_model: str = "flat"
    per_student_minor: int | None = None
    student_count: int | None = None
    custom_entitlements: dict | None = None
    custom_quotas: dict | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    notes: str = ""
    deal_name: str = ""


class CatalogSubscriptionChange(BaseModel):
    plan_key: str
    billing_cycle: str = "monthly"


class SubscriptionStatusUpdate(BaseModel):
    status: str | None = None  # active | trialing | past_due | cancelled
    dunning_step: str | None = None
    reason: str = ""


class DealCreate(BaseModel):
    tenant_id: int
    deal_name: str
    billing_cycle: str = "monthly"
    # Flat period price. For per_student, this is optional cache of (rate × students).
    price_minor: int = 0
    currency: str = "NGN"
    pricing_model: str = "flat"  # flat | per_student
    per_student_minor: int | None = None
    student_count: int | None = None
    custom_entitlements: dict = {}
    custom_quotas: dict = {}
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    term_start_dates: list[datetime] | None = None
    term_interval_months: int | None = Field(default=None, ge=1, le=24)
    term_count: int = Field(default=3, ge=1, le=24)
    notes: str = ""
    create_invoice: bool = True


class DealUpdate(BaseModel):
    deal_name: str | None = None
    billing_cycle: str | None = None
    price_minor: int | None = None
    currency: str | None = None
    pricing_model: str | None = None
    per_student_minor: int | None = None
    student_count: int | None = None
    custom_entitlements: dict | None = None
    custom_quotas: dict | None = None
    contract_start: datetime | None = None
    contract_end: datetime | None = None
    term_start_dates: list[datetime] | None = None
    term_interval_months: int | None = Field(default=None, ge=1, le=24)
    term_count: int | None = Field(default=None, ge=1, le=24)
    notes: str | None = None
    status: str | None = None
    approval_status: str | None = None
    reason: str = ""


class DealTemplateOut(ORMModel):
    id: int
    name: str
    description: str
    price_minor: int
    currency: str
    billing_cycle: str
    pricing_model: str = "flat"
    per_student_minor: int | None = None
    student_count: int | None = None
    entitlements: dict | None
    quotas: dict | None
    is_active: bool
    created_at: datetime


class DealTemplateCreate(BaseModel):
    name: str
    description: str = ""
    price_minor: int = 0
    currency: str = "NGN"
    billing_cycle: str = "monthly"
    pricing_model: str = "flat"
    per_student_minor: int | None = None
    student_count: int | None = None
    entitlements: dict = {}
    quotas: dict = {}


class DealCloneFromTemplate(BaseModel):
    tenant_id: int
    deal_name: str = ""
    notes: str = ""
    student_count: int | None = None
    contract_start: datetime | None = None
    term_start_dates: list[datetime] | None = None
    term_interval_months: int | None = Field(default=None, ge=1, le=24)
    term_count: int = Field(default=3, ge=1, le=24)
    create_invoice: bool = False
    submit_for_approval: bool = True


class CourtesyUnlockRequest(BaseModel):
    tenant_id: int
    duration_hours: int = Field(default=72, ge=1, le=168)
    reason: str = ""


class SaasInvoiceOut(ORMModel):
    id: int
    tenant_id: int
    subscription_id: int | None
    number: str
    period_key: str = ""
    amount_minor: int
    amount_paid_minor: int = 0
    balance_minor: int = 0
    currency: str
    status: str
    period_start: datetime | None
    period_end: datetime | None
    due_at: datetime | None
    paid_at: datetime | None
    notes: str
    tax_minor: int = 0
    tax_label: str = "VAT"
    created_at: datetime


class SaasInvoiceCreate(BaseModel):
    tenant_id: int
    subscription_id: int | None = None
    amount_minor: int | None = None
    currency: str = "NGN"
    due_days: int = 14
    notes: str = ""
    tax_minor: int = 0
    tax_label: str = "VAT"


class SaasPaymentCreate(BaseModel):
    amount_minor: int
    method: str = "manual"
    provider_id: str = ""
    provider_reference: str = ""
    notes: str = ""


class SaasPaymentOut(ORMModel):
    id: int
    invoice_id: int
    tenant_id: int
    amount_minor: int
    currency: str
    method: str
    provider_id: str
    provider_reference: str
    recorded_by: str
    notes: str
    created_at: datetime


class FeeSplitAllocation(BaseModel):
    destination: str = Field(min_length=1, max_length=128)
    percentage_bps: int = Field(ge=1, le=10000)
    account_reference: str = Field(default="", max_length=128)


class FeeSplitRuleOut(ORMModel):
    id: int
    tenant_id: int
    fee_type: str
    provider_id: str
    currency: str
    allocations: list[FeeSplitAllocation]
    is_active: bool
    created_by: str
    created_at: datetime


class FeeSplitRuleSet(BaseModel):
    tenant_id: int
    fee_type: str = Field(default="school_fees", min_length=1, max_length=64)
    provider_id: str = Field(default="", max_length=64)
    currency: str = Field(default="NGN", min_length=3, max_length=8)
    allocations: list[FeeSplitAllocation] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_allocations(self) -> "FeeSplitRuleSet":
        if self.provider_id.strip().lower() == "kira":
            raise ValueError("KIRA is not an enabled fee provider")
        destinations = [item.destination.lower() for item in self.allocations]
        if len(destinations) != len(set(destinations)):
            raise ValueError("fee split destinations must be unique")
        if sum(item.percentage_bps for item in self.allocations) != 10000:
            raise ValueError("fee split allocations must total 10000 basis points")
        return self


class PayrollAccountOut(ORMModel):
    id: int
    tenant_id: int
    account_role: str
    provider_id: str
    account_reference: str
    currency: str
    is_active: bool
    created_by: str
    created_at: datetime


class PayrollAccountSet(BaseModel):
    tenant_id: int
    account_role: str = "salary"
    provider_id: str = Field(default="", max_length=64)
    account_reference: str = Field(min_length=1, max_length=128)
    currency: str = Field(default="NGN", min_length=3, max_length=8)

    @model_validator(mode="after")
    def validate_role(self) -> "PayrollAccountSet":
        if self.account_role not in ("salary", "operating"):
            raise ValueError("account_role must be salary or operating")
        return self


class PayrollRunOut(ORMModel):
    id: int
    tenant_id: int
    period_key: str
    provider_id: str
    salary_account_id: int
    gross_minor: int
    currency: str
    status: str
    created_by: str
    approved_by: str
    external_reference: str
    notes: str
    created_at: datetime


class PayrollRunCreate(BaseModel):
    tenant_id: int
    period_key: str = Field(min_length=1, max_length=32)
    provider_id: str = Field(default="", max_length=64)
    gross_minor: int = Field(gt=0)
    currency: str = Field(default="NGN", min_length=3, max_length=8)
    notes: str = ""


class PayrollRunDecision(BaseModel):
    approve: bool
    reason: str = ""


# --------------------------------------------------------------------- flags


class FlagOut(ORMModel):
    id: int
    key: str
    name: str
    description: str
    default_enabled: bool
    requires_providers: list
    min_plan: str | None
    status: str
    kill_switch: bool
    deprecation_note: str = ""
    removal_date: datetime | None = None
    deprecated_at: datetime | None = None


class FlagUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_enabled: bool | None = None
    min_plan: str | None = None
    status: str | None = None


class FlagDeprecateRequest(BaseModel):
    removal_date: datetime
    note: str = ""
    reason: str = ""


class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: str


class OverrideSet(BaseModel):
    feature_key: str
    scope: str  # global | plan | tenant
    plan_key: str | None = None
    tenant_id: int | None = None
    enabled: bool
    reason: str = ""


class OverrideOut(ORMModel):
    id: int
    feature_key: str
    scope: str
    plan_id: int | None
    tenant_id: int | None
    enabled: bool
    reason: str
    created_by: str
    updated_at: datetime


class CohortRolloutSet(BaseModel):
    feature_key: str
    percentage: int = Field(ge=0, le=100)
    cohort_tags: list[str] = []
    enabled: bool = True
    reason: str = ""


class CohortRolloutOut(ORMModel):
    id: int
    feature_key: str
    percentage: int
    cohort_tags: list
    enabled: bool
    reason: str
    created_by: str
    updated_at: datetime


# ----------------------------------------------------------------- providers


class ProviderConfigOut(ORMModel):
    id: int
    capability: str
    tenant_id: int | None
    provider_id: str
    mode: str
    settings: dict
    is_enabled: bool
    health_status: str
    updated_at: datetime


class ProviderConfigSet(BaseModel):
    capability: str
    provider_id: str
    tenant_id: int | None = None  # None = global default
    mode: str = "sandbox"
    settings: dict = {}
    reason: str = ""


class ProviderSecretSet(BaseModel):
    key: str  # e.g. secret_key, api_key
    value: str


class ProviderSecretOut(ORMModel):
    id: int
    key: str
    fingerprint: str
    version: int
    is_active: bool
    created_at: datetime


# -------------------------------------------------------------------- blocks


class BlockCreate(BaseModel):
    target_type: str  # user | email | phone | ip | device | capability
    value: str
    tenant_id: int | None = None
    action: str = "deny"
    reason: str
    evidence: str = ""
    severity: str = "medium"
    expires_at: datetime | None = None


class BlockOut(ORMModel):
    id: int
    target_type: str
    value: str
    tenant_id: int | None
    action: str
    reason: str
    severity: str
    expires_at: datetime | None
    is_active: bool
    created_by: str
    created_at: datetime


class BlacklistEntryOut(ORMModel):
    id: int
    tenant_id: int | None
    school_name_normalized: str
    slug: str
    admin_email: str
    email_domain: str
    phone: str
    reason: str
    severity: str
    permanent: bool
    is_active: bool
    created_by: str
    created_at: datetime


class RegistrationCheckRequest(BaseModel):
    school_name: str = ""
    slug: str = ""
    admin_email: str = ""
    phone: str = ""
    ip_address: str = ""
    install_token: str = ""
    device_hash: str = ""


class TrialOut(ORMModel):
    id: int
    tenant_id: int
    duration_days: int
    enabled: bool
    status: str
    started_at: datetime
    ends_at: datetime
    reminder_7_sent_at: datetime | None
    reminder_1_sent_at: datetime | None
    expired_at: datetime | None


class TrialUpdate(BaseModel):
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    ends_at: datetime | None = None
    enabled: bool | None = None
    reason: str = ""


# --------------------------------------------------------------- maintenance


class MaintenanceCreate(BaseModel):
    message: str
    severity: str = "info"
    starts_at: datetime
    ends_at: datetime
    tenant_ids: list[int] = []


class MaintenanceOut(ORMModel):
    id: int
    message: str
    severity: str
    starts_at: datetime
    ends_at: datetime
    tenant_ids: list
    is_active: bool


# --------------------------------------------------------------------- audit


class AuditOut(ORMModel):
    id: int
    actor_type: str
    actor_email: str
    action: str
    target_type: str
    target_id: str
    reason: str
    before: dict | None
    after: dict | None
    ip_address: str
    created_at: datetime


# ----------------------------------------------------------- service clients


class ServiceClientCreate(BaseModel):
    name: str
    scopes: list[str] = [
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
        "offboarding:write",
    ]
    environment: str = "development"


class ServiceClientOut(ORMModel):
    id: int
    client_id: str
    name: str
    scopes: list
    environment: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime


class ServiceClientCreated(ServiceClientOut):
    client_secret: str  # returned once at creation


class M2MTokenRequest(BaseModel):
    client_id: str
    client_secret: str


# -------------------------------------------------------------------- usage


class UsageMeterOut(ORMModel):
    id: int
    key: str
    name: str
    unit: str
    feature_key: str
    description: str


class UsageIngest(BaseModel):
    tenant_ref: str | None = None
    meter_key: str
    quantity: int = 1
    event_id: str = ""
    properties: dict = {}


# --------------------------------------------------------------------- abuse


class AbuseCaseCreate(BaseModel):
    tenant_id: int | None = None
    title: str
    severity: str = "medium"
    summary: str = ""


class AbuseCaseUpdate(BaseModel):
    status: str | None = None
    severity: str | None = None
    summary: str | None = None
    assigned_to: str | None = None
    reason: str = ""


class AbuseCaseOut(ORMModel):
    id: int
    tenant_id: int | None
    title: str
    status: str
    severity: str
    summary: str
    assigned_to: str
    created_by: str
    created_at: datetime


class AbuseSignalCreate(BaseModel):
    case_id: int | None = None
    tenant_id: int | None = None
    signal_type: str
    value: str = ""
    score: int = 1
    evidence: str = ""
    # Simple auto-block threshold stub: score >= threshold creates a block
    auto_block_threshold: int = 10


class AbuseSignalOut(ORMModel):
    id: int
    case_id: int | None
    tenant_id: int | None
    signal_type: str
    value: str
    score: int
    evidence: str
    auto_blocked: bool
    created_at: datetime


# --------------------------------------------------------------- support


class SupportSessionCreate(BaseModel):
    tenant_id: int
    ticket_id: str
    reason: str
    access_level: str = "read"  # read | elevated_pii | write_support
    duration_minutes: int = Field(default=60, ge=5, le=60)


class SupportSessionOut(ORMModel):
    id: int
    tenant_id: int
    ticket_id: str
    reason: str
    staff_email: str
    access_level: str
    starts_at: datetime
    ends_at: datetime
    is_active: bool
    revoked_at: datetime | None
    revoke_reason: str
    created_at: datetime


# ------------------------------------------------------------ customer tickets

class PublicContactCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str = Field(default="", max_length=64)
    organization: str = Field(default="", max_length=255)
    request_type: str = "contact"
    message: str = Field(min_length=10, max_length=10_000)
    website: str = Field(default="", max_length=255)


class PublicContactOut(BaseModel):
    ticket_number: str
    message: str


class TicketNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class TicketUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_to: str | None = None
    follow_up_at: datetime | None = None
    contacted: bool | None = None
    reason: str = ""


class TicketNoteOut(ORMModel):
    id: int
    ticket_id: int
    author_email: str
    body: str
    created_at: datetime


class TicketOut(ORMModel):
    id: int
    ticket_number: str
    request_type: str
    name: str
    email: str
    phone: str
    organization: str
    message: str
    status: str
    priority: str
    assigned_to: str
    follow_up_at: datetime | None
    contacted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    notes: list[TicketNoteOut] = []


# ----------------------------------------------------------- Phase C compliance


class DsrCreate(BaseModel):
    tenant_id: int | None = None
    request_type: str  # access | export | delete
    subject_email: EmailStr
    subject_name: str = ""
    notes: str = ""
    evidence_ref: str = ""
    due_at: datetime | None = None


class DsrUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    assigned_to: str | None = None
    export_uri: str | None = None
    reason: str = ""


class DsrOut(ORMModel):
    id: int
    tenant_id: int | None
    request_type: str
    subject_email: str
    subject_name: str
    status: str
    notes: str
    evidence_ref: str
    export_uri: str
    assigned_to: str
    due_at: datetime | None
    completed_at: datetime | None
    created_by: str
    created_at: datetime


class SubprocessorOut(ORMModel):
    id: int
    name: str
    purpose: str
    region: str
    dpa_url: str
    is_active: bool


class SubprocessorCreate(BaseModel):
    name: str
    purpose: str = ""
    region: str = "global"
    dpa_url: str = ""


class SubprocessorUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    region: str | None = None
    dpa_url: str | None = None
    is_active: bool | None = None
    reason: str = ""


class OffboardJobOut(ORMModel):
    id: int
    tenant_id: int
    status: str
    export_package_uri: str
    wipe_scheduled_at: datetime | None
    legal_hold_blocked: bool
    notes: str
    created_by: str
    created_at: datetime


class OffboardCreate(BaseModel):
    notes: str = ""
    wipe_in_days: int = Field(default=30, ge=1, le=365)
    reason: str = ""


# ----------------------------------------------------------- Phase C incidents


class StatusComponentOut(ORMModel):
    id: int
    key: str
    name: str
    description: str
    status: str
    sort_order: int
    is_public: bool
    updated_at: datetime


class StatusComponentUpdate(BaseModel):
    status: str | None = None
    description: str | None = None
    name: str | None = None
    is_public: bool | None = None


class IncidentCreate(BaseModel):
    title: str
    impact: str = "minor"
    summary: str = ""
    affected_components: list[str] = []
    is_public: bool = True
    initial_update: str = ""


class IncidentUpdate(BaseModel):
    status: str | None = None
    impact: str | None = None
    summary: str | None = None
    affected_components: list[str] | None = None
    is_public: bool | None = None
    postmortem_url: str | None = None
    update_message: str | None = None


class IncidentOut(ORMModel):
    id: int
    title: str
    status: str
    impact: str
    summary: str
    affected_components: list
    updates: list
    is_public: bool
    started_at: datetime
    resolved_at: datetime | None
    postmortem_url: str
    created_by: str
    created_at: datetime


# ----------------------------------------------------------- Phase C dual-control


class DualControlCreate(BaseModel):
    action: str
    target_type: str = ""
    target_id: str = ""
    payload: dict = {}
    reason: str


class DualControlDecide(BaseModel):
    approve: bool
    reason: str = ""


class DualControlOut(ORMModel):
    id: int
    action: str
    target_type: str
    target_id: str
    payload: dict
    reason: str
    status: str
    requested_by: str
    approved_by: str
    decision_reason: str
    expires_at: datetime | None
    decided_at: datetime | None
    executed_at: datetime | None
    created_at: datetime


# ----------------------------------------------------------- Phase C failover


class FailoverPolicySet(BaseModel):
    capability: str
    primary_provider_id: str
    secondary_provider_id: str
    tenant_id: int | None = None
    enabled: bool = True
    failure_threshold: int = Field(default=3, ge=1, le=100)
    cooldown_seconds: int = Field(default=300, ge=30, le=86400)
    reason: str = ""


class FailoverPolicyOut(ORMModel):
    id: int
    capability: str
    tenant_id: int | None
    primary_provider_id: str
    secondary_provider_id: str
    enabled: bool
    failure_threshold: int
    cooldown_seconds: int
    is_tripped: bool
    tripped_at: datetime | None
    consecutive_failures: int
    reason: str
    updated_at: datetime


class PaymentContextItemOut(BaseModel):
    contextKey: str
    label: str
    description: str
    enabled: bool
    buttonEnabled: bool
    featuresSatisfied: bool
    requiredFeatures: list[str]
    gatewayId: str | None = None
    allowedGateways: list[str]


class PaymentContextListOut(BaseModel):
    items: list[PaymentContextItemOut]


class PaymentContextUpdate(BaseModel):
    enabled: bool | None = None
    gatewayId: str | None = None
    clearGateway: bool = False


class PaymentGatewayItemOut(BaseModel):
    gatewayId: str
    label: str
    enabled: bool


class PaymentGatewayListOut(BaseModel):
    items: list[PaymentGatewayItemOut]


class PaymentGatewayUpdate(BaseModel):
    enabled: bool


class TutoringPolicyOut(BaseModel):
    platformFeePercent: float
    defaultCurrency: str
    allowExternalTutors: bool
    marketplaceEnabled: bool


class TutoringPolicySet(BaseModel):
    tenant_id: int
    platformFeePercent: float = Field(default=15.0, ge=0, le=100)
    defaultCurrency: str = Field(default="NGN", min_length=3, max_length=8)
    allowExternalTutors: bool = True
    marketplaceEnabled: bool = True
