"""Manual payments (cash, transfer, POS, cheque), reversals and two-person refunds."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.finance.payments import (
    Payment,
    PaymentMethod,
    Refund,
    RefundStatus,
    parse_method,
)
from app.domain.identity.roles import Realm
from app.domain.shared.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import ensure_student_in_scope, visible_students
from app.use_cases.finance.common import (
    MoneyView,
    check_currency,
    ensure_can_see,
    load_settings,
    post_payment,
    post_refund,
    release,
    require_student,
    settle,
)
from app.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    SchoolTime,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

RECEIPT_SERIES = "receipt"
MAX_KEY = 128


def receipt_number(value: int, year: int) -> str:
    return f"RCT-{year}-{value:06d}"


@dataclass(frozen=True, slots=True)
class RecordPaymentCommand:
    student_id: UUID
    amount_minor: int
    currency: str | None
    method: str
    reference: str | None
    received_on: date | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AllocationView:
    invoice_id: UUID
    amount: MoneyView


@dataclass(frozen=True, slots=True)
class PaymentView:
    id: UUID
    receipt_number: str
    student_id: UUID
    amount: MoneyView
    method: str
    reference: str | None
    received_on: date
    status: str
    allocated: MoneyView
    refunded: MoneyView
    unused_credit: MoneyView
    recorded_by: UUID
    recorded_at: datetime
    reversed_at: datetime | None
    reversal_reason: str | None
    allocations: list[AllocationView]
    replayed: bool = False

    @classmethod
    def of(
        cls, p: Payment, allocations: list[AllocationView] | None = None, *, replayed: bool = False
    ) -> PaymentView:
        return cls(
            id=p.id,
            receipt_number=p.receipt_number,
            student_id=p.student_id,
            amount=MoneyView.of(p.amount),
            method=p.method.value,
            reference=p.reference,
            received_on=p.received_on,
            status=p.status.value,
            allocated=MoneyView.of(p.allocated),
            refunded=MoneyView.of(p.refunded),
            unused_credit=MoneyView.of(p.available),
            recorded_by=p.recorded_by,
            recorded_at=p.recorded_at,
            reversed_at=p.reversed_at,
            reversal_reason=p.reversal_reason,
            allocations=allocations or [],
            replayed=replayed,
        )


@dataclass(frozen=True, slots=True)
class RefundView:
    id: UUID
    payment_id: UUID
    student_id: UUID
    amount: MoneyView
    method: str
    reason: str
    status: str
    requested_by: UUID
    requested_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None

    @classmethod
    def of(cls, r: Refund) -> RefundView:
        return cls(
            id=r.id,
            payment_id=r.payment_id,
            student_id=r.student_id,
            amount=MoneyView.of(r.amount),
            method=r.method.value,
            reason=r.reason,
            status=r.status.value,
            requested_by=r.requested_by,
            requested_at=r.requested_at,
            decided_by=r.decided_by,
            decided_at=r.decided_at,
            decision_note=r.decision_note,
        )


def _fingerprint(cmd: RecordPaymentCommand, currency: str, received_on: date) -> str:
    raw = "|".join(
        [
            str(cmd.student_id),
            str(cmd.amount_minor),
            currency,
            cmd.method,
            " ".join((cmd.reference or "").split()),
            received_on.isoformat(),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _key(value: str) -> str:
    key = value.strip()
    if not 8 <= len(key) <= MAX_KEY or not key.isprintable():
        raise ValidationFailed(
            "Send an Idempotency-Key header of 8 to 128 characters.",
            code="idempotency_key_required",
            details={"field": "Idempotency-Key"},
        )
    return key


async def _allocations(uow: UnitOfWork, payment_id: UUID) -> list[AllocationView]:
    return [
        AllocationView(invoice_id=a.invoice_id, amount=MoneyView.of(a.amount))
        for a in await uow.finance.live_allocations(payment_id=payment_id)
    ]


class Payments:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    async def record(self, actor: Principal, cmd: RecordPaymentCommand) -> PaymentView:
        """Record money received at the school. Repeating a request with the same
        Idempotency-Key returns the first result instead of recording it twice."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("payments:process", "fees:manage")
        key = _key(cmd.idempotency_key)
        method = parse_method(cmd.method)
        tenant_id = actor.school_tenant_id
        local_now = await self._school_time.now_local(tenant_id)
        received_on = cmd.received_on or local_now.date()
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await load_settings(uow, tenant_id)
            currency = check_currency(settings, cmd.currency)
            fingerprint = _fingerprint(cmd, currency, received_on)
            await require_student(uow, tenant_id, cmd.student_id)
            await ensure_student_in_scope(uow, actor, cmd.student_id)
            await uow.finance.lock_student(cmd.student_id)
            earlier = await uow.finance.payment_by_key(actor.user_id, key)
            if earlier is not None:
                if earlier.request_fingerprint != fingerprint:
                    raise Conflict(
                        "This Idempotency-Key was already used for a different payment.",
                        code="idempotency_key_reused",
                    )
                return PaymentView.of(earlier, await _allocations(uow, earlier.id), replayed=True)
            if received_on > local_now.date():
                raise ValidationFailed(
                    "The payment date can't be in the future.", details={"field": "received_on"}
                )
            number = receipt_number(
                await uow.finance.next_number(RECEIPT_SERIES), local_now.date().year
            )
            payment = Payment.receive(
                tenant_id=tenant_id,
                receipt_number=number,
                student_id=cmd.student_id,
                amount=Money.positive(cmd.amount_minor, currency),
                method=method,
                reference=cmd.reference,
                received_on=received_on,
                by=actor.user_id,
                now=local_now,
                idempotency_key=key,
                request_fingerprint=fingerprint,
            )
            payment.recorded_at = now
            await uow.finance.add_payment(payment)
            uow.track(payment)
            await post_payment(uow, payment, reverse=False, by=actor.user_id, now=now)
            await settle(
                uow, tenant_id=tenant_id, student_id=cmd.student_id, by=actor.user_id, now=now
            )
            fresh = await uow.finance.get_payment(payment.id)
            allocations = await _allocations(uow, payment.id)
            uow.audit(
                AuditEntry(
                    action="finance.payment_recorded",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="payment",
                    target_id=str(payment.id),
                    metadata={
                        "receipt_number": number,
                        "student_id": str(cmd.student_id),
                        "amount_minor": cmd.amount_minor,
                        "method": method.value,
                    },
                )
            )
            await uow.commit()
        return PaymentView.of(fresh or payment, allocations)

    async def reverse(self, actor: Principal, payment_id: UUID, *, reason: str) -> PaymentView:
        """For money that never arrived: a bounced cheque or a payment keyed in twice."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            found = await uow.finance.get_payment(payment_id)
            if found is None:
                raise NotFound("Payment not found.")
            await ensure_student_in_scope(uow, actor, found.student_id)
            if found.method is PaymentMethod.ONLINE:
                raise ValidationFailed("Online payments are reversed through the gateway.")
            await uow.finance.lock_student(found.student_id)
            await release(
                uow,
                tenant_id=tenant_id,
                student_id=found.student_id,
                payment_id=payment_id,
                by=actor.user_id,
                now=now,
            )
            payment = await uow.finance.get_payment(payment_id, for_update=True)
            if payment is None:  # pragma: no cover - locked above
                raise NotFound("Payment not found.")
            payment.reverse(reason=reason, by=actor.user_id, now=now)
            await uow.finance.save_payment(payment)
            await post_payment(uow, payment, reverse=True, by=actor.user_id, now=now)
            # Invoices this payment settled reopen; other credit may now cover them.
            await settle(
                uow, tenant_id=tenant_id, student_id=payment.student_id, by=actor.user_id, now=now
            )
            uow.audit(
                AuditEntry(
                    action="finance.payment_reversed",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="payment",
                    target_id=str(payment.id),
                    metadata={
                        "receipt_number": payment.receipt_number,
                        "amount_minor": payment.amount.amount_minor,
                        "reason": payment.reversal_reason,
                    },
                )
            )
            await uow.commit()
        return PaymentView.of(payment)

    async def get(self, actor: Principal, payment_id: UUID) -> PaymentView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            payment = await uow.finance.get_payment(payment_id)
            if payment is None:
                raise NotFound("Payment not found.")
            try:
                await ensure_can_see(
                    uow, actor, payment.student_id, staff_permission="payments:view"
                )
            except PermissionDenied as exc:
                raise NotFound("Payment not found.") from exc
            allocations = await _allocations(uow, payment.id)
        return PaymentView.of(payment, allocations)

    async def list_payments(
        self, actor: Principal, *, student_id: UUID | None, cursor: str | None, limit: int
    ) -> Page[PaymentView]:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            staff = actor.permissions.allows("payments:view")
            if not staff:
                student_id = student_id or actor.user_id
                await ensure_can_see(uow, actor, student_id, staff_permission="payments:view")
            elif student_id is not None:
                await ensure_student_in_scope(uow, actor, student_id)
            page = await uow.finance.list_payments(
                student_id=student_id, cursor=cursor, limit=max(1, min(limit, 100))
            )
            ids = {p.student_id for p in page.items}
            shown = await visible_students(uow, actor, ids) if staff else ids
        return Page(
            items=[PaymentView.of(p) for p in page.items if p.student_id in shown],
            next_cursor=page.next_cursor,
        )

    # ---- refunds -------------------------------------------------------------------------

    async def request_refund(
        self, actor: Principal, payment_id: UUID, *, amount_minor: int, method: str, reason: str
    ) -> RefundView:
        """Hold unused credit for a refund. A different person must approve it."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("refunds:process")
        refund_method = parse_method(method)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            found = await uow.finance.get_payment(payment_id)
            if found is None:
                raise NotFound("Payment not found.")
            await uow.finance.lock_student(found.student_id)
            payment = await uow.finance.get_payment(payment_id, for_update=True)
            if payment is None:  # pragma: no cover - locked above
                raise NotFound("Payment not found.")
            refund = Refund.request(
                payment=payment,
                amount=Money.positive(amount_minor, payment.amount.currency),
                method=refund_method,
                reason=reason,
                by=actor.user_id,
                now=now,
            )
            await uow.finance.save_payment(payment)
            await uow.finance.add_refund(refund)
            self._audit(uow, actor, "finance.refund_requested", refund)
            await uow.commit()
        return RefundView.of(refund)

    async def decide_refund(
        self, actor: Principal, refund_id: UUID, *, approve: bool, note: str | None
    ) -> RefundView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("refunds:process")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            found = await uow.finance.get_refund(refund_id)
            if found is None:
                raise NotFound("Refund not found.")
            await uow.finance.lock_student(found.student_id)
            refund = await uow.finance.get_refund(refund_id, for_update=True)
            payment = await uow.finance.get_payment(found.payment_id, for_update=True)
            if refund is None or payment is None:  # pragma: no cover - locked above
                raise NotFound("Refund not found.")
            if approve:
                refund.approve(payment, by=actor.user_id, now=now, note=note)
            else:
                refund.reject(payment, by=actor.user_id, now=now, note=note)
            await uow.finance.save_payment(payment)
            await uow.finance.save_refund(refund)
            if approve:
                await post_refund(uow, refund, by=actor.user_id, now=now)
            else:
                # Released credit goes back to work on any open invoices.
                await settle(
                    uow,
                    tenant_id=tenant_id,
                    student_id=refund.student_id,
                    by=actor.user_id,
                    now=now,
                )
            self._audit(
                uow,
                actor,
                "finance.refund_approved" if approve else "finance.refund_rejected",
                refund,
            )
            await uow.commit()
        return RefundView.of(refund)

    async def list_refunds(self, actor: Principal, *, status: str | None) -> list[RefundView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("refunds:process")
        try:
            status_filter = RefundStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            refunds = await uow.finance.list_refunds(status=status_filter, limit=200)
        return [RefundView.of(r) for r in refunds]

    def _audit(self, uow: UnitOfWork, actor: Principal, action: str, refund: Refund) -> None:
        uow.audit(
            AuditEntry(
                action=action,
                actor_id=actor.user_id,
                actor_realm=Realm.SCHOOL,
                tenant_id=refund.tenant_id,
                target_type="refund",
                target_id=str(refund.id),
                metadata={
                    "payment_id": str(refund.payment_id),
                    "student_id": str(refund.student_id),
                    "amount_minor": refund.amount.amount_minor,
                    "reason": refund.reason,
                },
            )
        )
