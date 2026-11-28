"""Online fee payments: connect the school's provider account, pay, confirm, reconcile.

The provider is picked by name from ``PaymentGateways`` (Paystack today), so adding or switching
providers is an adapter change only. Calls to the provider never happen inside a database
transaction: a slow provider must not hold row locks.

A payment is credited only after the provider's API confirms it. Three paths lead there and all
are safe to repeat: the signed webhook, the parent returning from the payment page, and the
reconciliation job that checks pending checkouts. The checkout row is locked while settling, and a
checkout can produce one payment only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog

from app.domain.finance.online import (
    Checkout,
    CheckoutStatus,
    GatewayAccount,
    KeyMode,
    parse_provider,
)
from app.domain.finance.payments import Payment, PaymentMethod
from app.domain.identity.roles import Realm
from app.domain.shared.errors import (
    DomainError,
    FeatureDisabled,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.use_cases.finance.common import load_settings, post_payment, require_student, settle
from app.use_cases.finance.payments import RECEIPT_SERIES, receipt_number
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import (
    AuditEntry,
    CheckoutRequest,
    Clock,
    GatewayCredentials,
    PaymentGateways,
    RateLimiter,
    SchoolTime,
    Scope,
    SecretBox,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

_log = structlog.get_logger(__name__)

#: Reconciliation leaves fresh checkouts alone; the parent may still be on the payment page.
RECONCILE_AFTER = timedelta(minutes=5)
RECONCILE_BATCH = 200
_OFF = "This school doesn't take online payments. Pay by bank transfer and upload the receipt."


@dataclass(frozen=True, slots=True)
class GatewayView:
    provider: str
    mode: str
    public_key: str
    key_hint: str
    connected_at: datetime
    webhook_url: str

    @classmethod
    def of(cls, account: GatewayAccount, webhook_url: str) -> GatewayView:
        return cls(
            provider=account.provider.value,
            mode=account.mode.value,
            public_key=account.public_key,
            key_hint=account.key_hint,
            connected_at=account.connected_at,
            webhook_url=webhook_url,
        )


@dataclass(frozen=True, slots=True)
class CheckoutView:
    id: UUID
    reference: str
    provider: str
    status: str
    student_id: UUID
    amount_minor: int
    currency: str
    paid_minor: int | None
    authorization_url: str | None
    payment_id: UUID | None
    failure_reason: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, c: Checkout) -> CheckoutView:
        return cls(
            id=c.id,
            reference=c.reference,
            provider=c.provider.value,
            status=c.status.value,
            student_id=c.student_id,
            amount_minor=c.amount.amount_minor,
            currency=c.amount.currency,
            paid_minor=c.paid_amount.amount_minor if c.paid_amount else None,
            # Only a live checkout offers its payment page.
            authorization_url=c.authorization_url if c.status is CheckoutStatus.PENDING else None,
            payment_id=c.payment_id,
            failure_reason=c.failure_reason,
            created_at=c.created_at,
            completed_at=c.completed_at,
        )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    checked: int = 0
    succeeded: int = 0
    failed: int = 0
    abandoned: int = 0
    errors: int = 0


class OnlinePayments:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        gateways: PaymentGateways,
        box: SecretBox,
        clock: Clock,
        school_time: SchoolTime,
        limiter: RateLimiter,
        callback_url: str,
        public_api_url: str,
        allow_test_keys: bool,
    ) -> None:
        self._uow_factory = uow_factory
        self._gateways = gateways
        self._box = box
        self._clock = clock
        self._school_time = school_time
        self._limiter = limiter
        self._callback_url = callback_url
        self._public_api_url = public_api_url.rstrip("/")
        self._allow_test_keys = allow_test_keys

    def providers(self) -> list[str]:
        return self._gateways.providers()

    def webhook_url(self, provider: str, tenant_id: UUID) -> str:
        return f"{self._public_api_url}/api/v1/webhooks/payments/{provider}/{tenant_id}"

    def _credentials(self, account: GatewayAccount) -> GatewayCredentials:
        return GatewayCredentials(
            public_key=account.public_key,
            secret_key=self._box.decrypt(account.secret_key_ciphertext).decode(),
        )

    # ---- connecting the school's account -------------------------------------------------

    async def connect(
        self, actor: Principal, *, provider: str, public_key: str, secret_key: str
    ) -> GatewayView:
        """Check the keys with the provider, then store them (the secret key encrypted)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        await self._limiter.hit(f"gateway:connect:{tenant_id}", limit=10, window_seconds=3600)
        name = parse_provider(provider)
        gateway = self._gateways.get(name.value)
        secret_key, public_key = secret_key.strip(), public_key.strip()
        mode = KeyMode(gateway.key_mode(secret_key))
        if mode is KeyMode.TEST and not self._allow_test_keys:
            raise ValidationFailed(
                "Use your live keys. Test keys can't take real payments.",
                details={"field": "secret_key"},
            )
        await gateway.check_credentials(
            GatewayCredentials(public_key=public_key, secret_key=secret_key)
        )
        account = GatewayAccount.connect(
            tenant_id=tenant_id,
            provider=name,
            public_key=public_key,
            secret_key=secret_key,
            secret_key_ciphertext=self._box.encrypt(secret_key.encode()),
            mode=mode,
            by=actor.user_id,
            now=self._clock.now(),
        )
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.online_payments.save_gateway(account)
            uow.audit(
                audit.by(
                    actor,
                    "finance.gateway_connected",
                    target_type="payment_gateway",
                    target_id=name.value,
                    provider=name.value,
                    mode=mode.value,
                    key_hint=account.key_hint,
                )
            )
            await uow.commit()
        return GatewayView.of(account, self.webhook_url(name.value, tenant_id))

    async def disconnect(self, actor: Principal) -> None:
        """Stop online payments at once. Pending checkouts still settle if the parent paid."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            account = await uow.online_payments.get_gateway()
            if account is None:
                raise NotFound("No payment provider is connected.")
            await uow.online_payments.deactivate_gateways()
            settings = await load_settings(uow, tenant_id)
            was_on = settings.online_payments_enabled
            settings.online_payments_enabled = False
            await uow.finance.save_settings(settings)
            uow.audit(
                audit.by(
                    actor,
                    "finance.gateway_disconnected",
                    target_type="payment_gateway",
                    target_id=account.provider.value,
                    provider=account.provider.value,
                    online_payments_turned_off=was_on,
                )
            )
            await uow.commit()

    async def gateway(self, actor: Principal) -> GatewayView | None:
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            account = await uow.online_payments.get_gateway()
        if account is None:
            return None
        return GatewayView.of(account, self.webhook_url(account.provider.value, tenant_id))

    # ---- paying --------------------------------------------------------------------------

    async def start_checkout(
        self, actor: Principal, *, student_id: UUID, amount_minor: int | None
    ) -> CheckoutView:
        """Open the provider's payment page for a parent (or the student) to pay fees.

        The amount defaults to everything owed, and can't be more than that. Asking again within
        30 minutes for the same student and amount returns the same page.
        """
        actor.require_realm(Realm.SCHOOL)
        actor.require("payments:process")
        tenant_id = actor.school_tenant_id
        await self._limiter.hit(f"checkout:user:{actor.user_id}", limit=10, window_seconds=600)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await load_settings(uow, tenant_id)
            account = await uow.online_payments.get_gateway()
            if not settings.online_payments_enabled or account is None:
                raise FeatureDisabled(_OFF, code="online_payments_off")
            await require_student(uow, tenant_id, student_id)
            if not await is_guardian_or_self(uow, actor, student_id):
                raise PermissionDenied("You can only pay for your own children.")
            due = (await uow.finance.student_balance(student_id, settings.currency)).net_due
            if due.amount_minor <= 0:
                raise ValidationFailed("There's nothing to pay right now.", code="nothing_due")
            amount = Money.positive(amount_minor, settings.currency) if amount_minor else due
            if amount.amount_minor > due.amount_minor:
                raise ValidationFailed(
                    "That's more than is owed.",
                    code="amount_over_balance",
                    details={"field": "amount_minor", "due_minor": due.amount_minor},
                )
            recent = await uow.online_payments.recent_checkout(actor.user_id, student_id)
            if recent is not None and recent.can_reuse(
                student_id=student_id, amount=amount, now=now
            ):
                return CheckoutView.of(recent)
            payer = await uow.users.get(actor.user_id)
            if payer is None:  # pragma: no cover - the principal's account exists
                raise NotFound("Account not found.")
            checkout = Checkout.start(
                tenant_id=tenant_id,
                provider=account.provider,
                student_id=student_id,
                payer_id=actor.user_id,
                payer_email=payer.email.value,
                amount=amount,
                now=now,
            )
            await uow.online_payments.add_checkout(checkout)
            uow.audit(
                audit.by(
                    actor,
                    "finance.checkout_started",
                    target_type="checkout",
                    target_id=checkout.id,
                    reference=checkout.reference,
                    student_id=str(student_id),
                    amount_minor=amount.amount_minor,
                    currency=amount.currency,
                    provider=account.provider.value,
                )
            )
            await uow.commit()

        gateway = self._gateways.get(account.provider.value)
        try:
            session = await gateway.initialize(
                self._credentials(account),
                CheckoutRequest(
                    reference=checkout.reference,
                    amount_minor=amount.amount_minor,
                    currency=amount.currency,
                    email=checkout.payer_email,
                    callback_url=self._callback_url,
                    metadata={
                        "school_id": str(tenant_id),
                        "student_id": str(student_id),
                        "checkout_id": str(checkout.id),
                    },
                ),
            )
        except DomainError as exc:
            await self._close_failed(tenant_id, checkout.id, exc.message)
            raise
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            checkout.open(session.authorization_url)
            await uow.online_payments.save_checkout(checkout)
            # audit: not needed. Stores the provider's page link for the checkout audited above.
            await uow.commit()
        return CheckoutView.of(checkout)

    async def _close_failed(self, tenant_id: UUID, checkout_id: UUID, reason: str) -> None:
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            checkout = await uow.online_payments.get_checkout(checkout_id, for_update=True)
            if checkout is None:  # pragma: no cover - created just before
                return
            checkout.fail(reason, now=self._clock.now())
            await uow.online_payments.save_checkout(checkout)
            uow.audit(_entry("finance.checkout_failed", checkout, reason=reason))
            await uow.commit()

    async def confirm(self, actor: Principal, checkout_id: UUID) -> CheckoutView:
        """Called when the parent comes back from the payment page: ask the provider now."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        await self._limiter.hit(f"checkout:confirm:{actor.user_id}", limit=30, window_seconds=600)
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            checkout = await self._visible(uow, actor, checkout_id)
        return CheckoutView.of(await self._sync(tenant_id, checkout.id))

    async def get(self, actor: Principal, checkout_id: UUID) -> CheckoutView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return CheckoutView.of(await self._visible(uow, actor, checkout_id))

    async def list(
        self, actor: Principal, *, student_id: UUID | None, cursor: str | None, limit: int | None
    ) -> Page[CheckoutView]:
        """Staff see every checkout; parents see their own children's."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            if actor.permissions.allows("payments:view"):
                ids = [student_id] if student_id else None
            elif student_id is not None and await is_guardian_or_self(uow, actor, student_id):
                ids = [student_id]
            elif student_id is None:
                ids = [link.student_id for link in await uow.guardians.children_of(actor.user_id)]
                if actor.user_id not in ids:
                    ids.append(actor.user_id)
            else:
                raise PermissionDenied("You can only see your own family's payments.")
            page = await uow.online_payments.list_checkouts(
                student_ids=ids, cursor=cursor, limit=clamp_limit(limit)
            )
        return Page(items=[CheckoutView.of(c) for c in page.items], next_cursor=page.next_cursor)

    async def _visible(self, uow: UnitOfWork, actor: Principal, checkout_id: UUID) -> Checkout:
        checkout = await uow.online_payments.get_checkout(checkout_id)
        if checkout is None:
            raise NotFound("Payment not found.")
        if checkout.payer_id == actor.user_id or actor.permissions.allows("payments:view"):
            return checkout
        if await is_guardian_or_self(uow, actor, checkout.student_id):
            return checkout
        raise NotFound("Payment not found.")

    # ---- provider notifications ----------------------------------------------------------

    async def handle_webhook(
        self, *, provider: str, tenant_id: UUID, headers: Mapping[str, str], body: bytes
    ) -> bool:
        """Returns True when the event was about one of our checkouts. Forged events raise."""
        name = parse_provider(provider)
        gateway = self._gateways.get(name.value)
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            # A school that disconnected still gets events for payments made before.
            account = await uow.online_payments.get_gateway(name, include_inactive=True)
            if account is None:
                raise NotFound("Unknown payment account.")
            notice = gateway.read_webhook(self._credentials(account), headers, body)
            if notice.event != "charge.success" or not notice.reference:
                return False
            checkout = await uow.online_payments.get_checkout_by_reference(notice.reference)
        if checkout is None:
            return False
        await self._sync(tenant_id, checkout.id)
        return True

    async def reconcile(self) -> ReconcileReport:
        """Check every pending checkout older than a few minutes with its provider.

        Catches payments whose webhook never arrived, and closes checkouts nobody paid.
        """
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            pending = await uow.online_payments.pending_checkouts(
                older_than=now - RECONCILE_AFTER, limit=RECONCILE_BATCH
            )
        counts = {"succeeded": 0, "failed": 0, "abandoned": 0, "errors": 0}
        for item in pending:
            try:
                result = await self._sync(item.tenant_id, item.id)
            except Exception:
                _log.exception("payments.reconcile_failed", checkout_id=str(item.id))
                counts["errors"] += 1
                continue
            if result.status.value in counts:
                counts[result.status.value] += 1
        return ReconcileReport(checked=len(pending), **counts)

    # ---- settling ------------------------------------------------------------------------

    async def _sync(self, tenant_id: UUID, checkout_id: UUID) -> Checkout:
        """Bring one checkout in line with the provider. Safe to call any number of times."""
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            checkout = await uow.online_payments.get_checkout(checkout_id)
            if checkout is None:
                raise NotFound("Payment not found.")
            if checkout.status not in {CheckoutStatus.PENDING, CheckoutStatus.ABANDONED}:
                return checkout
            account = await uow.online_payments.get_gateway(
                checkout.provider, include_inactive=True
            )
        if account is None:
            return checkout
        txn = await self._gateways.get(checkout.provider.value).verify(
            self._credentials(account), checkout.reference
        )
        now = self._clock.now()
        local_now = await self._school_time.now_local(tenant_id)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.finance.lock_student(checkout.student_id)
            locked = await uow.online_payments.get_checkout(checkout_id, for_update=True)
            if locked is None:  # pragma: no cover - read above
                raise NotFound("Payment not found.")
            checkout = locked
            if checkout.status not in {CheckoutStatus.PENDING, CheckoutStatus.ABANDONED}:
                return checkout
            if txn.status == "success":
                if txn.currency != checkout.amount.currency or txn.amount_minor <= 0:
                    checkout.flag_for_review(
                        f"Provider reported {txn.amount_minor} {txn.currency or '?'}",
                        provider_transaction_id=txn.transaction_id,
                        now=now,
                    )
                    uow.audit(
                        _entry(
                            "finance.checkout_needs_review",
                            checkout,
                            reported_minor=txn.amount_minor,
                            reported_currency=txn.currency,
                        )
                    )
                else:
                    payment_id = await self._record_payment(
                        uow, checkout, txn.amount_minor, local_now, now
                    )
                    checkout.succeed(
                        paid=Money(txn.amount_minor, txn.currency),
                        payment_id=payment_id,
                        provider_transaction_id=txn.transaction_id,
                        channel=txn.channel,
                        now=now,
                    )
                    uow.track(checkout)
                    uow.audit(
                        _entry(
                            "finance.online_payment_received",
                            checkout,
                            reference=checkout.reference,
                            payment_id=str(checkout.payment_id),
                            amount_minor=txn.amount_minor,
                            expected_minor=checkout.amount.amount_minor,
                            channel=txn.channel,
                        )
                    )
            elif txn.status == "failed":
                checkout.fail(txn.gateway_response or "Declined by the provider.", now=now)
                uow.audit(
                    _entry("finance.checkout_failed", checkout, reason=checkout.failure_reason)
                )
            elif now >= checkout.expires_at and checkout.status is CheckoutStatus.PENDING:
                checkout.abandon(now=now)
                uow.audit(_entry("finance.checkout_abandoned", checkout))
            else:
                return checkout
            await uow.online_payments.save_checkout(checkout)
            await uow.commit()
        return checkout

    async def _record_payment(
        self,
        uow: UnitOfWork,
        checkout: Checkout,
        amount_minor: int,
        local_now: datetime,
        now: datetime,
    ) -> UUID:
        payment_id = new_id()
        number = receipt_number(await uow.finance.next_number(RECEIPT_SERIES), local_now.year)
        payment = Payment.receive(
            payment_id=payment_id,
            tenant_id=checkout.tenant_id,
            receipt_number=number,
            student_id=checkout.student_id,
            amount=Money(amount_minor, checkout.amount.currency),
            method=PaymentMethod.ONLINE,
            reference=checkout.reference,
            received_on=local_now.date(),
            by=checkout.payer_id,
            now=local_now,
            idempotency_key=f"checkout:{checkout.id}",
        )
        payment.recorded_at = now
        await uow.finance.add_payment(payment)
        uow.track(payment)
        await post_payment(uow, payment, reverse=False, by=checkout.payer_id, now=now)
        await settle(
            uow,
            tenant_id=checkout.tenant_id,
            student_id=checkout.student_id,
            by=checkout.payer_id,
            now=now,
        )
        return payment_id


def _entry(action: str, checkout: Checkout, **metadata: object) -> AuditEntry:
    """Provider-driven events are recorded as the payer's, since the payer started them."""
    return audit.system(
        action,
        target_type="checkout",
        target_id=checkout.id,
        tenant_id=checkout.tenant_id,
        actor_id=checkout.payer_id,
        actor_realm=Realm.SCHOOL,
        **metadata,
    )
