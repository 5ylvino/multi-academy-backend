"""Platform billing: schools pay the platform for their plan.

The billing cycle (run by the worker, or on demand from the owner console) does five things, each
safe to repeat and each in its own short transaction:

1. Gives every serving school a subscription (schools that predate billing get one now).
2. Issues the next period's invoice a week before the period starts. Free plans roll forward.
3. Reminds schools of overdue invoices every few days.
4. Suspends a school whose invoice is still unpaid after the grace period.
5. Checks unfinished online payments with the provider (catches missed webhooks).

Paying is possible three ways: online (Paystack, with the platform's own account), by bank
transfer that the owner console records, or by a waiver. A payment starts the period, turns a
trial into a paid account, and lifts a suspension that billing made. Suspensions made for any
other reason stay.

A suspended school can't sign in, so every invoice email carries a private pay link that works
without signing in. The link's token is an HMAC of the invoice id, so nothing secret is stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from app.domain.identity.roles import Realm
from app.domain.platform.feature_flags import Plan
from app.domain.shared.errors import FeatureDisabled, NotFound, ValidationFailed
from app.domain.shared.value_objects import Page
from app.domain.subscriptions.billing import (
    ISSUE_AHEAD,
    NON_PAYMENT_PREFIX,
    InvoiceStatus,
    PlatformInvoice,
    SettlementMethod,
    Subscription,
    SubscriptionStatus,
    add_months,
    invoice_number,
    new_checkout_reference,
)
from app.domain.tenancy.tenant import Tenant, TenantStatus
from app.use_cases.shared import audit, cache_keys
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import (
    AuditEntry,
    Cache,
    CheckoutRequest,
    Clock,
    GatewayCredentials,
    PaymentGateways,
    RateLimiter,
    Scope,
    SecretHasher,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

_log = structlog.get_logger(__name__)

BATCH = 200
#: A started payment page is offered again for this long instead of opening a new one.
CHECKOUT_REUSE = timedelta(minutes=30)
#: An unfinished checkout older than this is given up on (the school can start a new one).
CHECKOUT_LIFETIME = timedelta(hours=24)
#: Reconciliation leaves fresh checkouts alone; the school may still be on the payment page.
RECONCILE_AFTER = timedelta(minutes=5)
_NO_ONLINE = (
    "Online payment isn't available right now. Pay by bank transfer using the details shown."
)


@dataclass(frozen=True, slots=True)
class PlatformPaymentConfig:
    """How schools pay the platform. ``credentials`` is None when online payment is off."""

    provider: str
    credentials: GatewayCredentials | None
    #: The school app page behind the pay link; the provider also returns the payer here.
    pay_page_url: str
    bank_transfer_details: str | None = None


@dataclass(frozen=True, slots=True)
class InvoiceView:
    id: UUID
    school_id: UUID
    number: str
    plan_code: str
    period_start: datetime
    period_end: datetime
    student_count: int
    currency: str
    base_minor: int
    per_student_minor: int
    total_minor: int
    status: str
    issued_at: datetime
    due_at: datetime
    suspends_at: datetime
    overdue: bool
    paid_at: datetime | None
    settlement_method: str | None
    settlement_reference: str | None
    note: str | None
    #: The payment page, while a recent online checkout is open.
    authorization_url: str | None

    @classmethod
    def of(cls, i: PlatformInvoice, now: datetime) -> InvoiceView:
        live = (
            i.status is InvoiceStatus.OPEN
            and i.authorization_url is not None
            and i.checkout_started_at is not None
            and now - i.checkout_started_at < CHECKOUT_REUSE
        )
        return cls(
            id=i.id,
            school_id=i.tenant_id,
            number=i.number,
            plan_code=i.plan_code,
            period_start=i.period_start,
            period_end=i.period_end,
            student_count=i.student_count,
            currency=i.total.currency,
            base_minor=i.base.amount_minor,
            per_student_minor=i.per_student.amount_minor,
            total_minor=i.total.amount_minor,
            status=i.status.value,
            issued_at=i.issued_at,
            due_at=i.due_at,
            suspends_at=i.suspends_at,
            overdue=i.is_overdue(now),
            paid_at=i.paid_at,
            settlement_method=i.settlement_method.value if i.settlement_method else None,
            settlement_reference=i.settlement_reference,
            note=i.note,
            authorization_url=i.authorization_url if live else None,
        )


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    school_id: UUID
    school_status: str
    plan_code: str
    plan_name: str
    status: str
    is_free: bool
    currency: str
    base_price_minor: int
    price_per_student_minor: int
    billing_interval_months: int
    active_students: int
    #: What the next invoice would be if issued now.
    estimated_next_minor: int
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    open_invoices: list[InvoiceView]
    pay_online: bool
    bank_transfer_details: str | None


@dataclass(frozen=True, slots=True)
class PublicInvoiceView:
    """What the pay link shows: enough to pay, nothing more."""

    school_name: str
    invoice: InvoiceView
    pay_online: bool
    bank_transfer_details: str | None


@dataclass(slots=True)
class CycleReport:
    subscriptions_created: int = 0
    invoices_issued: int = 0
    periods_renewed: int = 0
    reminders_sent: int = 0
    schools_suspended: int = 0
    payments_confirmed: int = 0
    errors: int = 0
    _seen: set[str] = field(default_factory=set, repr=False)


class PlatformBilling:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        gateways: PaymentGateways,
        payment: PlatformPaymentConfig,
        secret_hasher: SecretHasher,
        cache: Cache,
        clock: Clock,
        limiter: RateLimiter,
    ) -> None:
        self._uow_factory = uow_factory
        self._gateways = gateways
        self._payment = payment
        self._hasher = secret_hasher
        self._cache = cache
        self._clock = clock
        self._limiter = limiter

    # ---- pay links -----------------------------------------------------------------------

    def pay_token(self, invoice_id: UUID) -> str:
        return self._hasher.hash(f"platform-invoice-pay:{invoice_id}")

    def pay_link(self, invoice_id: UUID) -> str:
        base = self._payment.pay_page_url
        return f"{base}?invoice={invoice_id}&token={self.pay_token(invoice_id)}"

    def _check_token(self, invoice_id: UUID, token: str) -> None:
        if not self._hasher.matches(f"platform-invoice-pay:{invoice_id}", token.strip()):
            raise NotFound("This payment link isn't valid. Use the link in the latest email.")

    @property
    def _online(self) -> bool:
        return self._payment.credentials is not None

    # ---- the school's own view -----------------------------------------------------------

    async def subscription(self, actor: Principal) -> SubscriptionView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("organization:view")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            return await self._subscription_view(uow, tenant_id)

    async def invoices(
        self, actor: Principal, *, cursor: str | None, limit: int | None
    ) -> Page[InvoiceView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("organization:view")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            page = await uow.subscriptions.list_invoices(
                tenant_id=tenant_id, status=None, cursor=cursor, limit=clamp_limit(limit)
            )
        now = self._clock.now()
        return Page(
            items=[InvoiceView.of(i, now) for i in page.items], next_cursor=page.next_cursor
        )

    async def pay(self, actor: Principal, invoice_id: UUID) -> InvoiceView:
        """Open the payment page for one of the school's invoices."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("organization:update")
        await self._limiter.hit(f"platform-pay:user:{actor.user_id}", limit=10, window_seconds=600)
        invoice = await self._own_invoice(actor.school_tenant_id, invoice_id)
        return await self._start_checkout(invoice, actor=actor)

    async def confirm(self, actor: Principal, invoice_id: UUID) -> InvoiceView:
        """Called when the school returns from the payment page: ask the provider now."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("organization:view")
        await self._limiter.hit(
            f"platform-pay:confirm:{actor.user_id}", limit=30, window_seconds=600
        )
        invoice = await self._own_invoice(actor.school_tenant_id, invoice_id)
        return InvoiceView.of(await self._sync_latest(invoice), self._clock.now())

    async def _own_invoice(self, tenant_id: UUID, invoice_id: UUID) -> PlatformInvoice:
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            invoice = await uow.subscriptions.get_invoice(invoice_id)
        if invoice is None or invoice.tenant_id != tenant_id:
            raise NotFound("Invoice not found.")
        return invoice

    # ---- the pay link (works while suspended, no sign-in) --------------------------------

    async def public_invoice(
        self, invoice_id: UUID, token: str, *, client_ip: str | None
    ) -> PublicInvoiceView:
        await self._limiter.hit(f"platform-pay:ip:{client_ip}", limit=60, window_seconds=600)
        self._check_token(invoice_id, token)
        invoice, tenant = await self._invoice_and_school(invoice_id)
        return self._public_view(invoice, tenant)

    async def public_pay(
        self, invoice_id: UUID, token: str, *, client_ip: str | None
    ) -> PublicInvoiceView:
        await self._limiter.hit(f"platform-pay:ip:{client_ip}", limit=20, window_seconds=600)
        self._check_token(invoice_id, token)
        invoice, tenant = await self._invoice_and_school(invoice_id)
        await self._start_checkout(invoice, actor=None)
        invoice, tenant = await self._invoice_and_school(invoice_id)
        return self._public_view(invoice, tenant)

    async def public_confirm(
        self, invoice_id: UUID, token: str, *, client_ip: str | None
    ) -> PublicInvoiceView:
        await self._limiter.hit(f"platform-pay:ip:{client_ip}", limit=60, window_seconds=600)
        self._check_token(invoice_id, token)
        invoice, tenant = await self._invoice_and_school(invoice_id)
        invoice = await self._sync_latest(invoice)
        return self._public_view(invoice, tenant)

    async def _invoice_and_school(self, invoice_id: UUID) -> tuple[PlatformInvoice, Tenant]:
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            invoice = await uow.subscriptions.get_invoice(invoice_id)
            tenant = await uow.tenants.get(invoice.tenant_id) if invoice else None
        if invoice is None or tenant is None:
            raise NotFound("This payment link isn't valid. Use the link in the latest email.")
        return invoice, tenant

    def _public_view(self, invoice: PlatformInvoice, tenant: Tenant) -> PublicInvoiceView:
        return PublicInvoiceView(
            school_name=tenant.name,
            invoice=InvoiceView.of(invoice, self._clock.now()),
            pay_online=self._online,
            bank_transfer_details=self._payment.bank_transfer_details,
        )

    # ---- owner console -------------------------------------------------------------------

    async def school_subscription(self, actor: Principal, tenant_id: UUID) -> SubscriptionView:
        actor.require_realm(Realm.PLATFORM)
        actor.require("subscriptions:read")
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            return await self._subscription_view(uow, tenant_id)

    async def list_invoices(
        self,
        actor: Principal,
        *,
        tenant_id: UUID | None,
        status: str | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[InvoiceView]:
        actor.require_realm(Realm.PLATFORM)
        actor.require("billing:read")
        try:
            status_filter = InvoiceStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            page = await uow.subscriptions.list_invoices(
                tenant_id=tenant_id, status=status_filter, cursor=cursor, limit=clamp_limit(limit)
            )
        now = self._clock.now()
        return Page(
            items=[InvoiceView.of(i, now) for i in page.items], next_cursor=page.next_cursor
        )

    async def record_payment(
        self,
        actor: Principal,
        invoice_id: UUID,
        *,
        method: str,
        reference: str | None,
        note: str | None,
    ) -> InvoiceView:
        """Record a bank transfer, or waive the invoice (a note saying why is required)."""
        actor.require_realm(Realm.PLATFORM)
        actor.require("billing:write")
        try:
            settlement = SettlementMethod(method)
        except ValueError as exc:
            raise ValidationFailed("Unknown payment method.", details={"field": "method"}) from exc
        if settlement is SettlementMethod.ONLINE:
            raise ValidationFailed(
                "Online payments are recorded automatically.", details={"field": "method"}
            )
        if settlement is SettlementMethod.BANK_TRANSFER and not (reference or "").strip():
            raise ValidationFailed(
                "Enter the bank transfer reference.", details={"field": "reference"}
            )
        invoice = await self._settle(
            invoice_id,
            method=settlement,
            reference=reference,
            note=note,
            by=actor.user_id,
            actor=actor,
        )
        return InvoiceView.of(invoice, self._clock.now())

    async def void(self, actor: Principal, invoice_id: UUID, *, reason: str) -> InvoiceView:
        """Cancel an open invoice. The next billing cycle issues a fresh one for the period."""
        actor.require_realm(Realm.PLATFORM)
        actor.require("billing:write")
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            invoice = await uow.subscriptions.get_invoice(invoice_id, for_update=True)
            if invoice is None:
                raise NotFound("Invoice not found.")
            invoice.void(reason, by=actor.user_id, now=now)
            await uow.subscriptions.save_invoice(invoice)
            uow.audit(
                audit.by(
                    actor,
                    "subscription.invoice_voided",
                    target_type="platform_invoice",
                    target_id=invoice.id,
                    tenant_id=invoice.tenant_id,
                    number=invoice.number,
                    total_minor=invoice.total.amount_minor,
                    reason=invoice.note,
                )
            )
            await uow.commit()
        return InvoiceView.of(invoice, now)

    async def change_plan(
        self, actor: Principal, tenant_id: UUID, *, plan_code: str
    ) -> SubscriptionView:
        """Move a school to another plan. Features change now; the price applies from the next
        invoice (void an open invoice to re-issue it at the new price)."""
        actor.require_realm(Realm.PLATFORM)
        actor.require("subscriptions:write")
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            tenant = await uow.tenants.get(tenant_id)
            if tenant is None:
                raise NotFound("School not found.")
            plan = await uow.plans.get(plan_code.strip())
            if plan is None or not plan.active:
                raise ValidationFailed("Pick an active plan.", details={"field": "plan_code"})
            previous = tenant.plan_code
            tenant.change_plan(plan.code)
            await uow.tenants.save(tenant)
            subscription = await self._ensure_subscription(uow, tenant, now)
            subscription.change_plan(plan.code, now)
            await uow.subscriptions.save(subscription)
            uow.audit(
                audit.by(
                    actor,
                    "subscription.plan_changed",
                    target_type="tenant",
                    target_id=tenant.id,
                    tenant_id=tenant.id,
                    **{"from": previous, "to": plan.code},
                )
            )
            await uow.commit()
        await self._cache.delete(cache_keys.tenant_runtime(tenant_id))
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            return await self._subscription_view(uow, tenant_id)

    async def run_now(self, actor: Principal) -> CycleReport:
        actor.require_realm(Realm.PLATFORM)
        actor.require("billing:write")
        report = await self.run_cycle()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            uow.audit(
                audit.by(
                    actor,
                    "subscription.cycle_run",
                    target_type="platform",
                    target_id="billing",
                    **{k: v for k, v in report_fields(report).items() if v},
                )
            )
            await uow.commit()
        return report

    # ---- the billing cycle ---------------------------------------------------------------

    async def run_cycle(self) -> CycleReport:
        report = CycleReport()
        await self._create_missing(report)
        await self._issue_due(report)
        await self._send_reminders(report)
        await self._enforce(report)
        await self._reconcile(report)
        return report

    async def _create_missing(self, report: CycleReport) -> None:
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            tenant_ids = await uow.subscriptions.schools_without_subscription(limit=BATCH)
        for tenant_id in tenant_ids:
            try:
                async with self._uow_factory(Scope.platform_wide()) as uow:
                    tenant = await uow.tenants.get(tenant_id)
                    if tenant is None or await uow.subscriptions.get(tenant_id) is not None:
                        continue
                    await self._ensure_subscription(uow, tenant, self._clock.now())
                    await uow.commit()
                report.subscriptions_created += 1
            except Exception:
                _log.exception("billing.create_subscription_failed", tenant_id=str(tenant_id))
                report.errors += 1

    async def _issue_due(self, report: CycleReport) -> None:
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            due = await uow.subscriptions.due_for_invoice(before=now + ISSUE_AHEAD, limit=BATCH)
        for item in due:
            try:
                outcome = await self._issue_one(item.tenant_id)
            except Exception:
                _log.exception("billing.issue_failed", tenant_id=str(item.tenant_id))
                report.errors += 1
                continue
            if outcome == "issued":
                report.invoices_issued += 1
            elif outcome == "renewed":
                report.periods_renewed += 1

    async def _issue_one(self, tenant_id: UUID) -> str | None:
        now = self._clock.now()
        cleared_cache = False
        async with self._uow_factory(Scope.platform_wide()) as uow:
            subscription = await uow.subscriptions.get(tenant_id, for_update=True)
            tenant = await uow.tenants.get(tenant_id)
            if subscription is None or tenant is None or not tenant.is_serving:
                return None
            if subscription.status is SubscriptionStatus.CANCELLED:
                return None
            if subscription.current_period_end >= now + ISSUE_AHEAD:
                return None
            if await uow.subscriptions.next_period_invoice(
                tenant_id, after=subscription.current_period_end
            ):
                return None
            plan = await uow.plans.get(tenant.plan_code)
            if plan is None:
                _log.error("billing.plan_missing", tenant_id=str(tenant_id), plan=tenant.plan_code)
                return None
            if subscription.plan_code != plan.code:
                subscription.change_plan(plan.code, now)
            start, end = _next_period(subscription, plan, now)

            if plan.is_free:
                if now < subscription.current_period_end:
                    return None  # roll on the day the period starts, not a week early
                subscription.begin_period(start, end, now)
                await uow.subscriptions.save(subscription)
                if tenant.status is TenantStatus.TRIAL:
                    tenant.activate()
                    await uow.tenants.save(tenant)
                    uow.track(tenant)
                    cleared_cache = True
                uow.audit(
                    _system(
                        "subscription.period_renewed",
                        "tenant",
                        tenant.id,
                        tenant.id,
                        plan_code=plan.code,
                        period_start=start,
                        period_end=end,
                    )
                )
                await uow.commit()
                outcome = "renewed"
            else:
                students = await uow.subscriptions.count_active_students(tenant_id)
                number = invoice_number(await uow.subscriptions.next_invoice_number(), now.year)
                invoice = PlatformInvoice.issue(
                    tenant_id=tenant_id,
                    number=number,
                    plan_code=plan.code,
                    currency=plan.currency,
                    base_minor=plan.base_price_minor,
                    per_student_minor=plan.price_per_student_minor,
                    student_count=students,
                    period_start=start,
                    period_end=end,
                    now=now,
                )
                await uow.subscriptions.add_invoice(invoice)
                await uow.subscriptions.save(subscription)
                uow.audit(
                    _system(
                        "subscription.invoice_issued",
                        "platform_invoice",
                        invoice.id,
                        tenant_id,
                        number=number,
                        plan_code=plan.code,
                        students=students,
                        total_minor=invoice.total.amount_minor,
                        currency=plan.currency,
                        due_at=invoice.due_at,
                    )
                )
                if invoice.total.amount_minor == 0:
                    # Per-student plan, no active students: nothing to pay, nothing to chase.
                    cleared_cache = await self._apply_payment(
                        uow,
                        invoice,
                        method=SettlementMethod.WAIVED,
                        reference=None,
                        note="Nothing to pay: no active students.",
                        by=None,
                        actor=None,
                        now=now,
                    )
                else:
                    uow.track(invoice)
                await uow.commit()
                outcome = "issued"
        if cleared_cache:
            await self._cache.delete(cache_keys.tenant_runtime(tenant_id))
        return outcome

    async def _send_reminders(self, report: CycleReport) -> None:
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            due = await uow.subscriptions.reminders_due(now=now, limit=BATCH)
        for item in due:
            try:
                async with self._uow_factory(Scope.platform_wide()) as uow:
                    invoice = await uow.subscriptions.get_invoice(item.id, for_update=True)
                    if invoice is None or not invoice.should_remind(now):
                        continue
                    subscription = await uow.subscriptions.get(invoice.tenant_id, for_update=True)
                    if subscription is not None and subscription.mark_past_due(now):
                        await uow.subscriptions.save(subscription)
                    invoice.remind(now)
                    await uow.subscriptions.save_invoice(invoice)
                    uow.track(invoice)
                    uow.audit(
                        _system(
                            "subscription.invoice_reminder_sent",
                            "platform_invoice",
                            invoice.id,
                            invoice.tenant_id,
                            number=invoice.number,
                            suspends_at=invoice.suspends_at,
                        )
                    )
                    await uow.commit()
                report.reminders_sent += 1
            except Exception:
                _log.exception("billing.reminder_failed", invoice_id=str(item.id))
                report.errors += 1

    async def _enforce(self, report: CycleReport) -> None:
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            due = await uow.subscriptions.invoices_to_enforce(now=now, limit=BATCH)
        for item in due:
            if str(item.tenant_id) in report._seen:
                continue
            try:
                async with self._uow_factory(Scope.platform_wide()) as uow:
                    invoice = await uow.subscriptions.get_invoice(item.id, for_update=True)
                    tenant = await uow.tenants.get(item.tenant_id)
                    if invoice is None or tenant is None or not tenant.is_serving:
                        continue
                    if invoice.status is not InvoiceStatus.OPEN or now < invoice.suspends_at:
                        continue
                    previous = tenant.status
                    tenant.suspend(f"{NON_PAYMENT_PREFIX} {invoice.number}")
                    await uow.tenants.save(tenant)
                    uow.track(tenant)
                    uow.audit(
                        _system(
                            "tenant.suspended_for_non_payment",
                            "tenant",
                            tenant.id,
                            tenant.id,
                            number=invoice.number,
                            due_at=invoice.due_at,
                            **{"from": previous.value},
                        )
                    )
                    await uow.commit()
                await self._cache.delete(cache_keys.tenant_runtime(item.tenant_id))
                report._seen.add(str(item.tenant_id))
                report.schools_suspended += 1
            except Exception:
                _log.exception("billing.suspend_failed", invoice_id=str(item.id))
                report.errors += 1

    async def _reconcile(self, report: CycleReport) -> None:
        if not self._online:
            return
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            waiting = await uow.subscriptions.invoices_awaiting_payment(
                started_before=now - RECONCILE_AFTER, limit=BATCH
            )
        for invoice in waiting:
            try:
                result = await self._sync_latest(invoice)
            except Exception:
                _log.exception("billing.reconcile_failed", invoice_id=str(invoice.id))
                report.errors += 1
                continue
            if result.status is InvoiceStatus.PAID:
                report.payments_confirmed += 1

    # ---- online payment ------------------------------------------------------------------

    def _credentials(self) -> GatewayCredentials:
        if self._payment.credentials is None:
            raise FeatureDisabled(_NO_ONLINE, code="platform_online_payment_off")
        return self._payment.credentials

    async def _start_checkout(
        self, invoice: PlatformInvoice, *, actor: Principal | None
    ) -> InvoiceView:
        credentials = self._credentials()
        now = self._clock.now()
        if invoice.status is not InvoiceStatus.OPEN:
            raise ValidationFailed(
                f"This invoice is already {invoice.status.value}.", code="invoice_closed"
            )
        view = InvoiceView.of(invoice, now)
        if view.authorization_url:
            return view
        if invoice.checkout_reference:
            # The school may have paid on the previous page; never open a second one blindly.
            invoice = await self._sync(invoice.id, invoice.checkout_reference)
            if invoice.status is not InvoiceStatus.OPEN:
                return InvoiceView.of(invoice, now)
        reference = new_checkout_reference()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            locked = await uow.subscriptions.get_invoice(invoice.id, for_update=True)
            tenant = await uow.tenants.get(invoice.tenant_id)
            if locked is None or tenant is None:  # pragma: no cover - read above
                raise NotFound("Invoice not found.")
            if locked.status is not InvoiceStatus.OPEN:
                return InvoiceView.of(locked, now)
            await uow.subscriptions.record_checkout(
                reference=reference, tenant_id=locked.tenant_id, invoice_id=locked.id, now=now
            )
            uow.audit(
                self._entry(
                    actor,
                    "subscription.checkout_started",
                    locked,
                    reference=reference,
                    total_minor=locked.total.amount_minor,
                    via="console" if actor else "pay_link",
                )
            )
            await uow.commit()
            invoice, email = locked, tenant.contact_email

        session = await self._gateways.get(self._payment.provider).initialize(
            credentials,
            CheckoutRequest(
                reference=reference,
                amount_minor=invoice.total.amount_minor,
                currency=invoice.total.currency,
                email=email,
                callback_url=f"{self.pay_link(invoice.id)}&returned=1",
                metadata={
                    "kind": "platform_invoice",
                    "school_id": str(invoice.tenant_id),
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.number,
                },
            ),
        )
        async with self._uow_factory(Scope.platform_wide()) as uow:
            locked = await uow.subscriptions.get_invoice(invoice.id, for_update=True)
            if locked is None:  # pragma: no cover - read above
                raise NotFound("Invoice not found.")
            if locked.status is InvoiceStatus.OPEN:
                locked.start_checkout(reference, session.authorization_url, self._clock.now())
                await uow.subscriptions.save_invoice(locked)
                # audit: not needed. Stores the payment page for the checkout audited above.
                await uow.commit()
        return InvoiceView.of(locked, self._clock.now())

    async def handle_webhook(self, *, headers: Mapping[str, str], body: bytes) -> bool:
        """Returns True when the event was about one of our invoices. Forged events raise."""
        gateway = self._gateways.get(self._payment.provider)
        notice = gateway.read_webhook(self._credentials(), headers, body)
        if notice.event != "charge.success" or not notice.reference:
            return False
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            invoice = await uow.subscriptions.get_invoice_by_reference(notice.reference)
        if invoice is None:
            return False
        await self._sync(invoice.id, notice.reference)
        return True

    async def _sync_latest(self, invoice: PlatformInvoice) -> PlatformInvoice:
        if invoice.status is not InvoiceStatus.OPEN or not invoice.checkout_reference:
            return invoice
        if not self._online:
            return invoice
        return await self._sync(invoice.id, invoice.checkout_reference)

    async def _sync(self, invoice_id: UUID, reference: str) -> PlatformInvoice:
        """Ask the provider about one checkout and credit the invoice if it was paid."""
        txn = await self._gateways.get(self._payment.provider).verify(
            self._credentials(), reference
        )
        now = self._clock.now()
        if txn.status == "success":
            async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
                invoice = await uow.subscriptions.get_invoice(invoice_id)
            if invoice is None:  # pragma: no cover - callers found it
                raise NotFound("Invoice not found.")
            if invoice.status is InvoiceStatus.PAID:
                return invoice
            enough = (
                txn.currency == invoice.total.currency
                and txn.amount_minor >= invoice.total.amount_minor
            )
            if enough and invoice.status is InvoiceStatus.OPEN:
                return await self._settle(
                    invoice_id,
                    method=SettlementMethod.ONLINE,
                    reference=txn.transaction_id or reference,
                    note=None,
                    by=None,
                    actor=None,
                    channel=txn.channel,
                    checkout_reference=reference,
                )
            # Paid the wrong amount, or paid a void invoice: a person must look.
            async with self._uow_factory(Scope.platform_wide()) as uow:
                uow.audit(
                    _system(
                        "subscription.payment_mismatch",
                        "platform_invoice",
                        invoice.id,
                        invoice.tenant_id,
                        reference=reference,
                        reported_minor=txn.amount_minor,
                        reported_currency=txn.currency,
                        expected_minor=invoice.total.amount_minor,
                        invoice_status=invoice.status.value,
                    )
                )
                await uow.commit()
            _log.warning("billing.payment_mismatch", invoice_id=str(invoice.id))
            return invoice

        async with self._uow_factory(Scope.platform_wide()) as uow:
            invoice = await uow.subscriptions.get_invoice(invoice_id, for_update=True)
            if invoice is None:  # pragma: no cover - callers found it
                raise NotFound("Invoice not found.")
            stale = (
                invoice.checkout_started_at is not None
                and now - invoice.checkout_started_at >= CHECKOUT_LIFETIME
            )
            current = invoice.checkout_reference == reference
            if (
                current
                and invoice.status is InvoiceStatus.OPEN
                and (txn.status == "failed" or stale)
            ):
                # Stop checking it; the pay link opens a fresh page next time.
                invoice.checkout_reference = None
                invoice.authorization_url = None
                await uow.subscriptions.save_invoice(invoice)
                # audit: not needed. Clears a dead payment page; no money moved.
                await uow.commit()
        return invoice

    # ---- settling ------------------------------------------------------------------------

    async def _settle(
        self,
        invoice_id: UUID,
        *,
        method: SettlementMethod,
        reference: str | None,
        note: str | None,
        by: UUID | None,
        actor: Principal | None,
        channel: str | None = None,
        checkout_reference: str | None = None,
    ) -> PlatformInvoice:
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            invoice = await uow.subscriptions.get_invoice(invoice_id, for_update=True)
            if invoice is None:
                raise NotFound("Invoice not found.")
            if invoice.status is InvoiceStatus.PAID:
                return invoice
            changed = await self._apply_payment(
                uow,
                invoice,
                method=method,
                reference=reference,
                note=note,
                by=by,
                actor=actor,
                now=now,
                channel=channel,
                checkout_reference=checkout_reference,
            )
            uow.track(invoice)
            await uow.commit()
        if changed:
            await self._cache.delete(cache_keys.tenant_runtime(invoice.tenant_id))
        return invoice

    async def _apply_payment(
        self,
        uow: UnitOfWork,
        invoice: PlatformInvoice,
        *,
        method: SettlementMethod,
        reference: str | None,
        note: str | None,
        by: UUID | None,
        actor: Principal | None,
        now: datetime,
        channel: str | None = None,
        checkout_reference: str | None = None,
    ) -> bool:
        """Mark paid, start the period, and bring the school back if billing had stopped it.
        Returns True when the school's status changed."""
        if not invoice.settle(method=method, reference=reference, by=by, now=now, note=note):
            return False
        await uow.subscriptions.save_invoice(invoice)
        tenant = await uow.tenants.get(invoice.tenant_id)
        if tenant is None:  # pragma: no cover - foreign key
            raise NotFound("School not found.")
        subscription = await self._ensure_subscription(uow, tenant, now, lock=True)
        subscription.begin_period(invoice.period_start, invoice.period_end, now)
        await uow.subscriptions.save(subscription)
        uow.audit(
            self._entry(
                actor,
                "subscription.invoice_paid",
                invoice,
                number=invoice.number,
                method=method.value,
                reference=invoice.settlement_reference,
                checkout_reference=checkout_reference,
                channel=channel,
                total_minor=invoice.total.amount_minor,
                currency=invoice.total.currency,
                note=invoice.note,
            )
        )

        previous = tenant.status
        if tenant.status is TenantStatus.TRIAL:
            tenant.activate()
        elif (
            tenant.status is TenantStatus.SUSPENDED
            and (tenant.status_reason or "").startswith(NON_PAYMENT_PREFIX)
            and not await self._other_unpaid_past_grace(uow, invoice, now)
        ):
            tenant.activate()
            uow.audit(
                _system(
                    "tenant.reinstated_after_payment",
                    "tenant",
                    tenant.id,
                    tenant.id,
                    number=invoice.number,
                )
            )
        if tenant.status is previous:
            return False
        await uow.tenants.save(tenant)
        uow.track(tenant)
        return True

    async def _other_unpaid_past_grace(
        self, uow: UnitOfWork, paid: PlatformInvoice, now: datetime
    ) -> bool:
        page = await uow.subscriptions.list_invoices(
            tenant_id=paid.tenant_id, status=InvoiceStatus.OPEN, cursor=None, limit=50
        )
        return any(i.id != paid.id and now >= i.suspends_at for i in page.items)

    # ---- helpers -------------------------------------------------------------------------

    async def _ensure_subscription(
        self, uow: UnitOfWork, tenant: Tenant, now: datetime, *, lock: bool = False
    ) -> Subscription:
        existing = await uow.subscriptions.get(tenant.id, for_update=lock)
        if existing is not None:
            return existing
        subscription = new_subscription(tenant, now)
        await uow.subscriptions.save(subscription)
        uow.audit(
            _system(
                "subscription.started",
                "tenant",
                tenant.id,
                tenant.id,
                plan_code=tenant.plan_code,
                first_bill_at=subscription.current_period_end,
            )
        )
        return subscription

    async def _subscription_view(self, uow: UnitOfWork, tenant_id: UUID) -> SubscriptionView:
        tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFound("School not found.")
        now = self._clock.now()
        subscription = await uow.subscriptions.get(tenant_id) or new_subscription(tenant, now)
        plan = await uow.plans.get(tenant.plan_code) or Plan(
            code=tenant.plan_code, name=tenant.plan_code
        )
        students = await uow.subscriptions.count_active_students(tenant_id)
        open_page = await uow.subscriptions.list_invoices(
            tenant_id=tenant_id, status=InvoiceStatus.OPEN, cursor=None, limit=20
        )
        return SubscriptionView(
            school_id=tenant_id,
            school_status=tenant.status.value,
            plan_code=plan.code,
            plan_name=plan.name,
            status=subscription.status.value,
            is_free=plan.is_free,
            currency=plan.currency,
            base_price_minor=plan.base_price_minor,
            price_per_student_minor=plan.price_per_student_minor,
            billing_interval_months=plan.billing_interval_months,
            active_students=students,
            estimated_next_minor=plan.base_price_minor + plan.price_per_student_minor * students,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_ends_at=tenant.trial_ends_at,
            open_invoices=[InvoiceView.of(i, now) for i in open_page.items],
            pay_online=self._online,
            bank_transfer_details=self._payment.bank_transfer_details,
        )

    @staticmethod
    def _entry(
        actor: Principal | None, action: str, invoice: PlatformInvoice, **metadata: Any
    ) -> AuditEntry:
        if actor is not None:
            return audit.by(
                actor,
                action,
                target_type="platform_invoice",
                target_id=invoice.id,
                tenant_id=invoice.tenant_id,
                **metadata,
            )
        return _system(action, "platform_invoice", invoice.id, invoice.tenant_id, **metadata)


def new_subscription(tenant: Tenant, now: datetime) -> Subscription:
    """A new school is billed from the end of its trial. A school found without a subscription
    (it predates billing) gets a week's notice before its first period."""
    trial_end = tenant.trial_ends_at
    if tenant.status is TenantStatus.TRIAL and trial_end is not None and trial_end > now:
        return Subscription.create(
            tenant_id=tenant.id,
            plan_code=tenant.plan_code,
            trialing=True,
            started=tenant.created_at,
            first_bill_at=trial_end,
            now=now,
        )
    return Subscription.create(
        tenant_id=tenant.id,
        plan_code=tenant.plan_code,
        trialing=tenant.status is TenantStatus.TRIAL,
        started=now,
        first_bill_at=now + ISSUE_AHEAD,
        now=now,
    )


def _next_period(
    subscription: Subscription, plan: Plan, now: datetime
) -> tuple[datetime, datetime]:
    """Periods follow on from each other. After a long gap (a school back from suspension) the
    period starts today instead of billing for months nobody used."""
    start = subscription.current_period_end
    if add_months(start, plan.billing_interval_months) <= now:
        start = now
    return start, add_months(start, plan.billing_interval_months)


def _system(
    action: str, target_type: str, target_id: UUID, tenant_id: UUID, **metadata: Any
) -> AuditEntry:
    return audit.system(
        action, target_type=target_type, target_id=target_id, tenant_id=tenant_id, **metadata
    )


def report_fields(report: CycleReport) -> dict[str, Any]:
    return {
        "subscriptions_created": report.subscriptions_created,
        "invoices_issued": report.invoices_issued,
        "periods_renewed": report.periods_renewed,
        "reminders_sent": report.reminders_sent,
        "schools_suspended": report.schools_suspended,
        "payments_confirmed": report.payments_confirmed,
        "errors": report.errors,
    }
