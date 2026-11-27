"""Proof of payment: parents upload a receipt for money paid to the school directly, and the
bursar checks it against the bank statement before it counts as a payment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.finance.payments import Payment, PaymentMethod
from app.domain.finance.proofs import (
    MAX_OPEN_PER_STUDENT,
    PaymentProof,
    ProofFile,
    ProofStatus,
    detect_receipt_type,
    normalise_reference,
)
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import (
    Conflict,
    FeatureDisabled,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.use_cases.finance.common import (
    MoneyView,
    check_currency,
    ensure_can_see,
    load_settings,
    post_payment,
    require_student,
    settle,
)
from app.use_cases.finance.payments import RECEIPT_SERIES, receipt_number
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    ObjectStore,
    RateLimiter,
    SchoolTime,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

SUBMIT_LIMIT = 20
SUBMIT_WINDOW_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class SubmitProofCommand:
    student_id: UUID
    amount_minor: int
    currency: str | None
    method: str
    paid_on: date
    reference: str
    payer_name: str | None
    note: str | None
    invoice_id: UUID | None
    file_bytes: bytes


@dataclass(frozen=True, slots=True)
class ProofView:
    id: UUID
    student_id: UUID
    submitted_by: UUID
    submitted_at: datetime
    amount: MoneyView
    method: str
    paid_on: date
    reference: str
    payer_name: str | None
    note: str | None
    invoice_id: UUID | None
    file_type: str
    file_size: int
    status: str
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    approved_amount: MoneyView | None
    payment_id: UUID | None

    @classmethod
    def of(cls, p: PaymentProof) -> ProofView:
        return cls(
            id=p.id,
            student_id=p.student_id,
            submitted_by=p.submitted_by,
            submitted_at=p.submitted_at,
            amount=MoneyView.of(p.amount),
            method=p.method.value,
            paid_on=p.paid_on,
            reference=p.reference,
            payer_name=p.payer_name,
            note=p.note,
            invoice_id=p.invoice_id,
            file_type=p.file.content_type.value,
            file_size=p.file.size,
            status=p.status.value,
            reviewed_by=p.reviewed_by,
            reviewed_at=p.reviewed_at,
            review_note=p.review_note,
            approved_amount=MoneyView.of(p.approved_amount) if p.approved_amount else None,
            payment_id=p.payment_id,
        )


@dataclass(frozen=True, slots=True)
class ProofFileView:
    content: bytes
    content_type: str
    filename: str


def _method(value: str) -> PaymentMethod:
    try:
        return PaymentMethod(value)
    except ValueError as exc:
        raise ValidationFailed(
            "Choose bank transfer, POS or cheque.", details={"field": "method"}
        ) from exc


class PaymentProofs:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        school_time: SchoolTime,
        store: ObjectStore,
        limiter: RateLimiter,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time
        self._store = store
        self._limiter = limiter

    async def _may_submit(self, uow: UnitOfWork, actor: Principal, student_id: UUID) -> None:
        if actor.permissions.allows("fees:manage"):
            return  # the bursar uploading a receipt a parent sent by other means
        if actor.permissions.allows("payments:process") and await is_guardian_or_self(
            uow, actor, student_id
        ):
            return
        raise PermissionDenied("You can only send proof of payment for your own children.")

    async def submit(self, actor: Principal, cmd: SubmitProofCommand) -> ProofView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        await self._limiter.hit(
            f"proof:user:{actor.user_id}", limit=SUBMIT_LIMIT, window_seconds=SUBMIT_WINDOW_SECONDS
        )
        kind = detect_receipt_type(cmd.file_bytes)
        method = _method(cmd.method)
        reference_key = normalise_reference(cmd.reference)
        today = (await self._school_time.now_local(tenant_id)).date()
        now = self._clock.now()

        # 1. Check everything we can before storing any bytes.
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            settings = await load_settings(uow, tenant_id)
            if not settings.payment_proofs_enabled:
                raise FeatureDisabled(
                    "This school doesn't take uploaded receipts. Pay at the bursary instead.",
                    code="payment_proofs_disabled",
                )
            currency = check_currency(settings, cmd.currency)
            await require_student(uow, tenant_id, cmd.student_id)
            await self._may_submit(uow, actor, cmd.student_id)
            if cmd.invoice_id is not None:
                invoice = await uow.finance.get_invoice(cmd.invoice_id)
                if invoice is None or invoice.student_id != cmd.student_id:
                    raise NotFound("Invoice not found.")
            if await uow.finance.open_proofs_for_student(cmd.student_id) >= MAX_OPEN_PER_STUDENT:
                raise ValidationFailed(
                    "There are already 10 receipts waiting for review for this student.",
                    code="too_many_open_proofs",
                )
            if reference_key and await uow.finance.payment_reference_exists(reference_key):
                raise Conflict(
                    "The school has already recorded a payment with this reference.",
                    code="duplicate_reference",
                )

        proof_id = new_id()
        stored = ProofFile(
            key=PaymentProof.file_key(tenant_id, proof_id, kind),
            content_type=kind,
            size=len(cmd.file_bytes),
            sha256=hashlib.sha256(cmd.file_bytes).hexdigest(),
        )
        proof = PaymentProof.submit(
            proof_id=proof_id,
            tenant_id=tenant_id,
            student_id=cmd.student_id,
            by=actor.user_id,
            amount=Money.positive(cmd.amount_minor, currency),
            method=method,
            paid_on=cmd.paid_on,
            reference=cmd.reference,
            payer_name=cmd.payer_name,
            note=cmd.note,
            file=stored,
            invoice_id=cmd.invoice_id,
            today=today,
            now=now,
        )

        # 2. Store the file, then the record. If the record fails, remove the file.
        await self._store.put(stored.key, cmd.file_bytes, content_type=kind.value)
        try:
            async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
                await uow.finance.add_proof(proof)
                uow.track(proof)
                uow.audit(
                    AuditEntry(
                        action="finance.payment_proof_submitted",
                        actor_id=actor.user_id,
                        actor_realm=Realm.SCHOOL,
                        tenant_id=tenant_id,
                        target_type="payment_proof",
                        target_id=str(proof.id),
                        metadata={
                            "student_id": str(cmd.student_id),
                            "amount_minor": cmd.amount_minor,
                            "reference": proof.reference,
                        },
                    )
                )
                await uow.commit()
        except BaseException:
            await self._store.delete(stored.key)
            raise
        return ProofView.of(proof)

    async def _visible(self, uow: UnitOfWork, actor: Principal, proof_id: UUID) -> PaymentProof:
        proof = await uow.finance.get_proof(proof_id)
        if proof is None:
            raise NotFound("Proof of payment not found.")
        if proof.submitted_by == actor.user_id:
            return proof
        try:
            await ensure_can_see(uow, actor, proof.student_id, staff_permission="payments:view")
        except PermissionDenied as exc:
            raise NotFound("Proof of payment not found.") from exc
        return proof

    async def get(self, actor: Principal, proof_id: UUID) -> ProofView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return ProofView.of(await self._visible(uow, actor, proof_id))

    async def file(self, actor: Principal, proof_id: UUID) -> ProofFileView:
        actor.require_realm(Realm.SCHOOL)
        # Receipts hold bank details, so opening one is recorded like a change.
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            proof = await self._visible(uow, actor, proof_id)
            uow.audit(
                audit.by(
                    actor,
                    "finance.payment_proof_file_viewed",
                    target_type="payment_proof",
                    target_id=proof.id,
                    student_id=str(proof.student_id),
                )
            )
            await uow.commit()
        content = await self._store.get(proof.file.key)
        if hashlib.sha256(content).hexdigest() != proof.file.sha256:
            raise Conflict("The stored receipt doesn't match what was uploaded.")
        return ProofFileView(
            content=content,
            content_type=proof.file.content_type.value,
            filename=f"receipt-{proof.reference_key[:24] or proof.id}."
            f"{proof.file.content_type.extension}",
        )

    async def list(
        self,
        actor: Principal,
        *,
        status: str | None,
        student_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> Page[ProofView]:
        """Staff see the whole review queue. Parents see their children's, students their own."""
        actor.require_realm(Realm.SCHOOL)
        try:
            status_filter = ProofStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            student_ids: list[UUID] | None
            if actor.permissions.allows("payments:view"):
                student_ids = [student_id] if student_id else None
            else:
                allowed = {actor.user_id}
                if SchoolRole.PARENT.value in actor.roles:
                    allowed |= {
                        link.student_id for link in await uow.guardians.children_of(actor.user_id)
                    }
                if student_id is not None and student_id not in allowed:
                    raise PermissionDenied("You can only see your own family's receipts.")
                student_ids = [student_id] if student_id else sorted(allowed, key=str)
            page = await uow.finance.list_proofs(
                status=status_filter,
                student_ids=student_ids,
                cursor=cursor,
                limit=max(1, min(limit, 100)),
            )
        return Page(items=[ProofView.of(p) for p in page.items], next_cursor=page.next_cursor)

    async def approve(
        self, actor: Principal, proof_id: UUID, *, amount_minor: int | None, note: str | None
    ) -> ProofView:
        """The money is in the school's account: record it as a payment and settle invoices."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("payments:process", "fees:manage")
        tenant_id = actor.school_tenant_id
        local_now = await self._school_time.now_local(tenant_id)
        local_today = local_now.date()
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            found = await uow.finance.get_proof(proof_id)
            if found is None:
                raise NotFound("Proof of payment not found.")
            await uow.finance.lock_student(found.student_id)
            proof = await uow.finance.get_proof(proof_id, for_update=True)
            if proof is None:  # pragma: no cover - locked above
                raise NotFound("Proof of payment not found.")
            if await uow.finance.payment_reference_exists(proof.reference_key):
                raise Conflict(
                    "A payment with this reference is already recorded. Reject this one.",
                    code="duplicate_reference",
                )
            payment_id = new_id()
            received = proof.approve(
                by=actor.user_id,
                now=now,
                payment_id=payment_id,
                amount=Money(amount_minor, proof.amount.currency) if amount_minor else None,
                note=note,
            )
            number = receipt_number(await uow.finance.next_number(RECEIPT_SERIES), local_today.year)
            payment = Payment.receive(
                payment_id=payment_id,
                tenant_id=tenant_id,
                receipt_number=number,
                student_id=proof.student_id,
                amount=received,
                method=proof.method,
                reference=proof.reference,
                received_on=min(proof.paid_on, local_today),
                by=actor.user_id,
                # The school's local time: just after midnight in Lagos it's still yesterday in
                # UTC, and today's date must not count as "in the future".
                now=local_now,
                idempotency_key=f"proof:{proof.id}",
            )
            payment.recorded_at = now
            await uow.finance.add_payment(payment)
            uow.track(payment)
            await post_payment(uow, payment, reverse=False, by=actor.user_id, now=now)
            await uow.finance.save_proof(proof)
            uow.track(proof)
            await settle(
                uow, tenant_id=tenant_id, student_id=proof.student_id, by=actor.user_id, now=now
            )
            uow.audit(
                AuditEntry(
                    action="finance.payment_proof_approved",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="payment_proof",
                    target_id=str(proof.id),
                    metadata={
                        "payment_id": str(payment.id),
                        "receipt_number": number,
                        "claimed_minor": proof.amount.amount_minor,
                        "received_minor": received.amount_minor,
                    },
                )
            )
            await uow.commit()
        return ProofView.of(proof)

    async def reject(self, actor: Principal, proof_id: UUID, *, reason: str) -> ProofView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("payments:process", "fees:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            proof = await uow.finance.get_proof(proof_id, for_update=True)
            if proof is None:
                raise NotFound("Proof of payment not found.")
            proof.reject(by=actor.user_id, now=self._clock.now(), reason=reason)
            await uow.finance.save_proof(proof)
            uow.track(proof)
            uow.audit(
                AuditEntry(
                    action="finance.payment_proof_rejected",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="payment_proof",
                    target_id=str(proof.id),
                    metadata={"reason": proof.review_note},
                )
            )
            await uow.commit()
        return ProofView.of(proof)

    async def withdraw(self, actor: Principal, proof_id: UUID) -> ProofView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            proof = await uow.finance.get_proof(proof_id, for_update=True)
            if proof is None or proof.submitted_by != actor.user_id:
                raise NotFound("Proof of payment not found.")
            proof.withdraw(by=actor.user_id)
            await uow.finance.save_proof(proof)
            uow.audit(
                audit.by(
                    actor,
                    "finance.payment_proof_withdrawn",
                    target_type="payment_proof",
                    target_id=proof.id,
                    student_id=str(proof.student_id),
                )
            )
            await uow.commit()
        return ProofView.of(proof)
