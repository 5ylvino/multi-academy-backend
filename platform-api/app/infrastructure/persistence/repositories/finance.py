"""Finance repository. Every query is filtered by the scoped tenant as well as by RLS."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.finance.billing import (
    Invoice,
    InvoiceDiscount,
    InvoiceKind,
    InvoiceLine,
    InvoiceStatus,
    Scholarship,
    ScholarshipKind,
)
from app.domain.finance.catalog import BankAccount, FeeItem, FinanceSettings
from app.domain.finance.ledger import Account, JournalEntry, StudentBalance
from app.domain.finance.payments import (
    Allocation,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundStatus,
)
from app.domain.finance.proofs import (
    PaymentProof,
    ProofFile,
    ProofStatus,
    ReceiptType,
    normalise_reference,
)
from app.domain.shared.errors import Conflict, ValidationFailed
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.finance import (
    AllocationRow,
    FeeItemRow,
    FinanceCounterRow,
    FinanceSettingsRow,
    InvoiceRow,
    JournalEntryRow,
    JournalLineRow,
    PaymentProofRow,
    PaymentRow,
    RefundRow,
    ScholarshipRow,
)
from app.infrastructure.persistence.repositories.scope_state import ScopeState

_STALE = "This record was changed by someone else. Reload and try again."


def _encode(value: UUID) -> str:
    return base64.urlsafe_b64encode(value.bytes).decode().rstrip("=")


def _decode(cursor: str) -> UUID:
    try:
        return UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as exc:
        raise ValidationFailed("Cursor is not valid.", details={"field": "cursor"}) from exc


def _fee_item(r: FeeItemRow) -> FeeItem:
    return FeeItem(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        amount=Money(r.amount_minor, r.currency),
        school_level=r.school_level,
        class_id=r.class_id,
        terms=frozenset(r.terms),
        mandatory=r.mandatory,
        active=r.active,
        created_at=r.created_at,
    )


def _scholarship(r: ScholarshipRow) -> Scholarship:
    return Scholarship(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        session_id=r.session_id,
        name=r.name,
        kind=ScholarshipKind(r.kind),
        value=r.value,
        currency=r.currency,
        active=r.active,
        granted_by=r.granted_by,
        granted_at=r.granted_at,
        revoked_by=r.revoked_by,
        revoked_at=r.revoked_at,
    )


def _opt_uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def _invoice(r: InvoiceRow) -> Invoice:
    cur = r.currency
    return Invoice(
        id=r.id,
        tenant_id=r.tenant_id,
        number=r.number,
        kind=InvoiceKind(r.kind),
        student_id=r.student_id,
        class_id=r.class_id,
        session_id=r.session_id,
        term_id=r.term_id,
        currency=cur,
        lines=[
            InvoiceLine(
                description=str(x["description"]),
                amount=Money(int(x["amount_minor"]), cur),
                fee_item_id=_opt_uuid(x.get("fee_item_id")),
            )
            for x in r.lines
        ],
        discounts=[
            InvoiceDiscount(
                description=str(x["description"]),
                amount=Money(int(x["amount_minor"]), cur),
                scholarship_id=_opt_uuid(x.get("scholarship_id")),
            )
            for x in r.discounts
        ],
        status=InvoiceStatus(r.status),
        amount_paid=Money(r.amount_paid_minor, cur),
        due_on=r.due_on,
        issued_by=r.issued_by,
        issued_at=r.issued_at,
        voided_by=r.voided_by,
        voided_at=r.voided_at,
        void_reason=r.void_reason,
        version=r.version,
    )


def _payment(r: PaymentRow) -> Payment:
    cur = r.currency
    return Payment(
        id=r.id,
        tenant_id=r.tenant_id,
        receipt_number=r.receipt_number,
        student_id=r.student_id,
        amount=Money(r.amount_minor, cur),
        method=PaymentMethod(r.method),
        reference=r.reference,
        received_on=r.received_on,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
        allocated=Money(r.allocated_minor, cur),
        refunded=Money(r.refunded_minor, cur),
        held=Money(r.held_minor, cur),
        status=PaymentStatus(r.status),
        idempotency_key=r.idempotency_key,
        request_fingerprint=r.request_fingerprint,
        reversed_by=r.reversed_by,
        reversed_at=r.reversed_at,
        reversal_reason=r.reversal_reason,
        version=r.version,
    )


def _refund(r: RefundRow) -> Refund:
    return Refund(
        id=r.id,
        tenant_id=r.tenant_id,
        payment_id=r.payment_id,
        student_id=r.student_id,
        amount=Money(r.amount_minor, r.currency),
        method=PaymentMethod(r.method),
        reason=r.reason,
        status=RefundStatus(r.status),
        requested_by=r.requested_by,
        requested_at=r.requested_at,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
    )


def _proof(r: PaymentProofRow) -> PaymentProof:
    return PaymentProof(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        submitted_by=r.submitted_by,
        submitted_at=r.submitted_at,
        amount=Money(r.amount_minor, r.currency),
        method=PaymentMethod(r.method),
        paid_on=r.paid_on,
        reference=r.reference,
        reference_key=r.reference_key,
        payer_name=r.payer_name,
        note=r.note,
        file=ProofFile(
            key=r.file_key,
            content_type=ReceiptType(r.file_type),
            size=r.file_size,
            sha256=r.file_sha256,
        ),
        invoice_id=r.invoice_id,
        status=ProofStatus(r.status),
        reviewed_by=r.reviewed_by,
        reviewed_at=r.reviewed_at,
        review_note=r.review_note,
        approved_amount=(
            Money(r.approved_amount_minor, r.currency)
            if r.approved_amount_minor is not None
            else None
        ),
        payment_id=r.payment_id,
    )


class SqlFinanceRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Finance data needs a tenant scope.")
        return self._scope.tenant_id

    # ---- settings and numbering ----------------------------------------------------------

    async def get_settings(self) -> FinanceSettings | None:
        row = await self._s.get(FinanceSettingsRow, self._tid)
        if row is None:
            return None
        return FinanceSettings(
            id=row.tenant_id,
            tenant_id=row.tenant_id,
            currency=row.currency,
            default_due_days=row.default_due_days,
            online_payments_enabled=row.online_payments_enabled,
            payment_proofs_enabled=row.payment_proofs_enabled,
            payment_instructions=row.payment_instructions,
            bank_accounts=tuple(
                BankAccount(
                    bank_name=str(a["bank_name"]),
                    account_name=str(a["account_name"]),
                    account_number=str(a["account_number"]),
                )
                for a in row.bank_accounts
            ),
            version=row.version,
        )

    async def save_settings(self, settings: FinanceSettings) -> None:
        values = {
            "currency": settings.currency,
            "default_due_days": settings.default_due_days,
            "online_payments_enabled": settings.online_payments_enabled,
            "payment_proofs_enabled": settings.payment_proofs_enabled,
            "payment_instructions": settings.payment_instructions,
            "bank_accounts": [
                {
                    "bank_name": a.bank_name,
                    "account_name": a.account_name,
                    "account_number": a.account_number,
                }
                for a in settings.bank_accounts
            ],
            "version": settings.version + 1,
        }
        stmt = (
            pg_insert(FinanceSettingsRow)
            .values(tenant_id=self._tid, **values)
            .on_conflict_do_update(
                index_elements=["tenant_id"],
                set_={**values, "updated_at": datetime.now(UTC)},
                where=FinanceSettingsRow.version == settings.version,
            )
            .returning(FinanceSettingsRow.tenant_id)
        )
        if (await self._s.execute(stmt)).first() is None:
            raise Conflict(_STALE)
        settings.version += 1

    async def next_number(self, series: str) -> int:
        # Row lock on the counter: numbers are unique and, inside committed work, gap-free.
        stmt = (
            pg_insert(FinanceCounterRow)
            .values(tenant_id=self._tid, series=series, value=1)
            .on_conflict_do_update(
                index_elements=["tenant_id", "series"],
                set_={"value": FinanceCounterRow.value + 1},
            )
            .returning(FinanceCounterRow.value)
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def has_invoices(self) -> bool:
        return bool(await self._s.scalar(select(exists().where(InvoiceRow.tenant_id == self._tid))))

    async def lock_student(self, student_id: UUID) -> None:
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"finance:{self._tid}:{student_id}"},
        )

    # ---- catalog -------------------------------------------------------------------------

    def _fee_values(self, item: FeeItem) -> dict[str, Any]:
        return {
            "name": item.name,
            "amount_minor": item.amount.amount_minor,
            "currency": item.amount.currency,
            "school_level": item.school_level,
            "class_id": item.class_id,
            "terms": sorted(item.terms),
            "mandatory": item.mandatory,
            "active": item.active,
        }

    async def add_fee_item(self, item: FeeItem) -> None:
        self._s.add(
            FeeItemRow(
                tenant_id=self._tid,
                id=item.id,
                created_at=item.created_at,
                **self._fee_values(item),
            )
        )
        await self._s.flush()

    async def get_fee_item(self, item_id: UUID) -> FeeItem | None:
        row = await self._s.scalar(
            select(FeeItemRow).where(FeeItemRow.tenant_id == self._tid, FeeItemRow.id == item_id)
        )
        return _fee_item(row) if row else None

    async def save_fee_item(self, item: FeeItem) -> None:
        await self._s.execute(
            update(FeeItemRow)
            .where(FeeItemRow.tenant_id == self._tid, FeeItemRow.id == item.id)
            .values(**self._fee_values(item))
            .execution_options(synchronize_session=False)
        )

    async def list_fee_items(self, *, include_inactive: bool) -> list[FeeItem]:
        stmt = select(FeeItemRow).where(FeeItemRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(FeeItemRow.active.is_(True))
        rows = await self._s.scalars(stmt.order_by(FeeItemRow.name, FeeItemRow.id))
        return [_fee_item(r) for r in rows]

    # ---- scholarships --------------------------------------------------------------------

    async def add_scholarship(self, scholarship: Scholarship) -> None:
        s = scholarship
        self._s.add(
            ScholarshipRow(
                tenant_id=self._tid,
                id=s.id,
                student_id=s.student_id,
                session_id=s.session_id,
                name=s.name,
                kind=s.kind.value,
                value=s.value,
                currency=s.currency,
                active=s.active,
                granted_by=s.granted_by,
                granted_at=s.granted_at,
            )
        )
        await self._s.flush()

    async def get_scholarship(self, scholarship_id: UUID) -> Scholarship | None:
        row = await self._s.scalar(
            select(ScholarshipRow).where(
                ScholarshipRow.tenant_id == self._tid, ScholarshipRow.id == scholarship_id
            )
        )
        return _scholarship(row) if row else None

    async def save_scholarship(self, scholarship: Scholarship) -> None:
        await self._s.execute(
            update(ScholarshipRow)
            .where(ScholarshipRow.tenant_id == self._tid, ScholarshipRow.id == scholarship.id)
            .values(
                active=scholarship.active,
                revoked_by=scholarship.revoked_by,
                revoked_at=scholarship.revoked_at,
            )
            .execution_options(synchronize_session=False)
        )

    async def scholarships_for(
        self, *, student_id: UUID | None, session_id: UUID | None, active_only: bool
    ) -> list[Scholarship]:
        stmt = select(ScholarshipRow).where(ScholarshipRow.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(ScholarshipRow.student_id == student_id)
        if session_id is not None:
            stmt = stmt.where(ScholarshipRow.session_id == session_id)
        if active_only:
            stmt = stmt.where(ScholarshipRow.active.is_(True))
        rows = await self._s.scalars(stmt.order_by(ScholarshipRow.granted_at, ScholarshipRow.id))
        return [_scholarship(r) for r in rows]

    # ---- invoices ------------------------------------------------------------------------

    async def add_invoices(self, invoices: list[Invoice]) -> None:
        if not invoices:
            return
        rows = [
            {
                "tenant_id": self._tid,
                "id": i.id,
                "number": i.number,
                "kind": i.kind.value,
                "student_id": i.student_id,
                "class_id": i.class_id,
                "session_id": i.session_id,
                "term_id": i.term_id,
                "currency": i.currency,
                "lines": [
                    {
                        "description": x.description,
                        "amount_minor": x.amount.amount_minor,
                        "fee_item_id": str(x.fee_item_id) if x.fee_item_id else None,
                    }
                    for x in i.lines
                ],
                "discounts": [
                    {
                        "description": d.description,
                        "amount_minor": d.amount.amount_minor,
                        "scholarship_id": str(d.scholarship_id) if d.scholarship_id else None,
                    }
                    for d in i.discounts
                ],
                "subtotal_minor": i.subtotal.amount_minor,
                "total_minor": i.total.amount_minor,
                "amount_paid_minor": i.amount_paid.amount_minor,
                "status": i.status.value,
                "due_on": i.due_on,
                "issued_by": i.issued_by,
                "issued_at": i.issued_at,
                "version": 1,
            }
            for i in invoices
        ]
        try:
            for start in range(0, len(rows), 500):
                await self._s.execute(pg_insert(InvoiceRow).values(rows[start : start + 500]))
        except IntegrityError as exc:
            if "uq_finance_invoices_student_term" in str(exc.orig):
                raise Conflict(
                    "A student already has an invoice for this term.", code="already_billed"
                ) from exc
            raise
        for invoice in invoices:
            invoice.version = 1

    async def get_invoice(self, invoice_id: UUID, *, for_update: bool = False) -> Invoice | None:
        stmt = select(InvoiceRow).where(
            InvoiceRow.tenant_id == self._tid, InvoiceRow.id == invoice_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt.execution_options(populate_existing=True))).scalar()
        return _invoice(row) if row else None

    async def save_invoice(self, invoice: Invoice) -> None:
        result = await self._s.execute(
            update(InvoiceRow)
            .where(
                InvoiceRow.tenant_id == self._tid,
                InvoiceRow.id == invoice.id,
                InvoiceRow.version == invoice.version,
            )
            .values(
                status=invoice.status.value,
                amount_paid_minor=invoice.amount_paid.amount_minor,
                voided_by=invoice.voided_by,
                voided_at=invoice.voided_at,
                void_reason=invoice.void_reason,
                version=invoice.version + 1,
            )
            .returning(InvoiceRow.id)
            .execution_options(synchronize_session=False)
        )
        if result.first() is None:
            raise Conflict(_STALE)
        invoice.version += 1

    async def students_billed_for_term(self, term_id: UUID) -> set[UUID]:
        rows = await self._s.scalars(
            select(InvoiceRow.student_id).where(
                InvoiceRow.tenant_id == self._tid,
                InvoiceRow.term_id == term_id,
                InvoiceRow.kind == InvoiceKind.TERM.value,
                InvoiceRow.status != InvoiceStatus.VOID.value,
            )
        )
        return set(rows)

    async def open_invoices(self, student_id: UUID) -> list[Invoice]:
        rows = await self._s.scalars(
            select(InvoiceRow)
            .where(
                InvoiceRow.tenant_id == self._tid,
                InvoiceRow.student_id == student_id,
                InvoiceRow.status.in_(
                    [InvoiceStatus.ISSUED.value, InvoiceStatus.PARTIALLY_PAID.value]
                ),
            )
            .order_by(InvoiceRow.due_on, InvoiceRow.issued_at, InvoiceRow.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return [_invoice(r) for r in rows]

    async def list_invoices(
        self,
        *,
        student_id: UUID | None,
        term_id: UUID | None,
        status: InvoiceStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Invoice]:
        stmt = select(InvoiceRow).where(InvoiceRow.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(InvoiceRow.student_id == student_id)
        if term_id is not None:
            stmt = stmt.where(InvoiceRow.term_id == term_id)
        if status is not None:
            stmt = stmt.where(InvoiceRow.status == status.value)
        if cursor:
            stmt = stmt.where(InvoiceRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(InvoiceRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_invoice(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def invoices_of_student(self, student_id: UUID) -> list[Invoice]:
        rows = await self._s.scalars(
            select(InvoiceRow)
            .where(InvoiceRow.tenant_id == self._tid, InvoiceRow.student_id == student_id)
            .order_by(InvoiceRow.issued_at, InvoiceRow.id)
        )
        return [_invoice(r) for r in rows]

    # ---- payments and allocations --------------------------------------------------------

    async def add_payment(self, payment: Payment) -> None:
        p = payment
        try:
            await self._s.execute(
                pg_insert(PaymentRow).values(
                    tenant_id=self._tid,
                    id=p.id,
                    receipt_number=p.receipt_number,
                    student_id=p.student_id,
                    amount_minor=p.amount.amount_minor,
                    currency=p.amount.currency,
                    method=p.method.value,
                    reference=p.reference,
                    reference_key=normalise_reference(p.reference) if p.reference else None,
                    received_on=p.received_on,
                    recorded_by=p.recorded_by,
                    recorded_at=p.recorded_at,
                    allocated_minor=0,
                    refunded_minor=0,
                    held_minor=0,
                    status=p.status.value,
                    idempotency_key=p.idempotency_key,
                    request_fingerprint=p.request_fingerprint,
                    version=1,
                )
            )
        except IntegrityError as exc:
            if "uq_finance_payments_idempotency" in str(exc.orig):
                raise Conflict(
                    "This Idempotency-Key was already used for a different payment.",
                    code="idempotency_key_reused",
                ) from exc
            raise
        payment.version = 1

    async def get_payment(self, payment_id: UUID, *, for_update: bool = False) -> Payment | None:
        stmt = select(PaymentRow).where(
            PaymentRow.tenant_id == self._tid, PaymentRow.id == payment_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt.execution_options(populate_existing=True))).scalar()
        return _payment(row) if row else None

    async def payment_by_key(self, recorded_by: UUID, key: str) -> Payment | None:
        row = await self._s.scalar(
            select(PaymentRow).where(
                PaymentRow.tenant_id == self._tid,
                PaymentRow.recorded_by == recorded_by,
                PaymentRow.idempotency_key == key,
            )
        )
        return _payment(row) if row else None

    async def save_payment(self, payment: Payment) -> None:
        result = await self._s.execute(
            update(PaymentRow)
            .where(
                PaymentRow.tenant_id == self._tid,
                PaymentRow.id == payment.id,
                PaymentRow.version == payment.version,
            )
            .values(
                allocated_minor=payment.allocated.amount_minor,
                refunded_minor=payment.refunded.amount_minor,
                held_minor=payment.held.amount_minor,
                status=payment.status.value,
                reversed_by=payment.reversed_by,
                reversed_at=payment.reversed_at,
                reversal_reason=payment.reversal_reason,
                version=payment.version + 1,
            )
            .returning(PaymentRow.id)
            .execution_options(synchronize_session=False)
        )
        if result.first() is None:
            raise Conflict(_STALE)
        payment.version += 1

    async def payments_with_credit(self, student_id: UUID) -> list[Payment]:
        rows = await self._s.scalars(
            select(PaymentRow)
            .where(
                PaymentRow.tenant_id == self._tid,
                PaymentRow.student_id == student_id,
                PaymentRow.status == PaymentStatus.RECORDED.value,
                PaymentRow.amount_minor
                > PaymentRow.allocated_minor + PaymentRow.refunded_minor + PaymentRow.held_minor,
            )
            .order_by(PaymentRow.received_on, PaymentRow.recorded_at, PaymentRow.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return [_payment(r) for r in rows]

    async def payments_of_student(self, student_id: UUID) -> list[Payment]:
        rows = await self._s.scalars(
            select(PaymentRow)
            .where(PaymentRow.tenant_id == self._tid, PaymentRow.student_id == student_id)
            .order_by(PaymentRow.recorded_at, PaymentRow.id)
        )
        return [_payment(r) for r in rows]

    async def list_payments(
        self, *, student_id: UUID | None, cursor: str | None, limit: int
    ) -> Page[Payment]:
        stmt = select(PaymentRow).where(PaymentRow.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(PaymentRow.student_id == student_id)
        if cursor:
            stmt = stmt.where(PaymentRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(PaymentRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_payment(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def add_allocations(self, allocations: list[Allocation], now: datetime) -> None:
        if not allocations:
            return
        await self._s.execute(
            pg_insert(AllocationRow).values(
                [
                    {
                        "tenant_id": self._tid,
                        "id": new_id(),
                        "payment_id": a.payment_id,
                        "invoice_id": a.invoice_id,
                        "amount_minor": a.amount.amount_minor,
                        "currency": a.amount.currency,
                        "created_at": now,
                    }
                    for a in allocations
                ]
            )
        )

    def _live(self, payment_id: UUID | None, invoice_id: UUID | None) -> list[Any]:
        if payment_id is None and invoice_id is None:
            raise ValueError("Pass a payment or an invoice.")
        conditions: list[Any] = [
            AllocationRow.tenant_id == self._tid,
            AllocationRow.released_at.is_(None),
        ]
        if payment_id is not None:
            conditions.append(AllocationRow.payment_id == payment_id)
        if invoice_id is not None:
            conditions.append(AllocationRow.invoice_id == invoice_id)
        return conditions

    async def live_allocations(
        self, *, payment_id: UUID | None = None, invoice_id: UUID | None = None
    ) -> list[Allocation]:
        rows = await self._s.scalars(
            select(AllocationRow)
            .where(*self._live(payment_id, invoice_id))
            .order_by(AllocationRow.created_at, AllocationRow.id)
        )
        return [
            Allocation(
                payment_id=r.payment_id,
                invoice_id=r.invoice_id,
                amount=Money(r.amount_minor, r.currency),
            )
            for r in rows
        ]

    async def release_allocations(
        self, *, payment_id: UUID | None = None, invoice_id: UUID | None = None, now: datetime
    ) -> None:
        await self._s.execute(
            update(AllocationRow)
            .where(*self._live(payment_id, invoice_id))
            .values(released_at=now)
            .execution_options(synchronize_session=False)
        )

    # ---- refunds -------------------------------------------------------------------------

    async def add_refund(self, refund: Refund) -> None:
        r = refund
        self._s.add(
            RefundRow(
                tenant_id=self._tid,
                id=r.id,
                payment_id=r.payment_id,
                student_id=r.student_id,
                amount_minor=r.amount.amount_minor,
                currency=r.amount.currency,
                method=r.method.value,
                reason=r.reason,
                status=r.status.value,
                requested_by=r.requested_by,
                requested_at=r.requested_at,
            )
        )
        await self._s.flush()

    async def get_refund(self, refund_id: UUID, *, for_update: bool = False) -> Refund | None:
        stmt = select(RefundRow).where(RefundRow.tenant_id == self._tid, RefundRow.id == refund_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt.execution_options(populate_existing=True))).scalar()
        return _refund(row) if row else None

    async def save_refund(self, refund: Refund) -> None:
        await self._s.execute(
            update(RefundRow)
            .where(RefundRow.tenant_id == self._tid, RefundRow.id == refund.id)
            .values(
                status=refund.status.value,
                decided_by=refund.decided_by,
                decided_at=refund.decided_at,
                decision_note=refund.decision_note,
            )
            .execution_options(synchronize_session=False)
        )

    async def list_refunds(self, *, status: RefundStatus | None, limit: int) -> list[Refund]:
        stmt = select(RefundRow).where(RefundRow.tenant_id == self._tid)
        if status is not None:
            stmt = stmt.where(RefundRow.status == status.value)
        rows = await self._s.scalars(stmt.order_by(RefundRow.requested_at.desc()).limit(limit))
        return [_refund(r) for r in rows]

    async def refunds_of_student(self, student_id: UUID) -> list[Refund]:
        rows = await self._s.scalars(
            select(RefundRow)
            .where(RefundRow.tenant_id == self._tid, RefundRow.student_id == student_id)
            .order_by(RefundRow.requested_at)
        )
        return [_refund(r) for r in rows]

    async def payment_reference_exists(self, reference_key: str) -> bool:
        return bool(
            await self._s.scalar(
                select(
                    exists().where(
                        PaymentRow.tenant_id == self._tid,
                        PaymentRow.reference_key == reference_key,
                        PaymentRow.status == PaymentStatus.RECORDED.value,
                    )
                )
            )
        )

    # ---- proofs of payment ---------------------------------------------------------------

    async def add_proof(self, proof: PaymentProof) -> None:
        p = proof
        try:
            await self._s.execute(
                pg_insert(PaymentProofRow).values(
                    tenant_id=self._tid,
                    id=p.id,
                    student_id=p.student_id,
                    submitted_by=p.submitted_by,
                    submitted_at=p.submitted_at,
                    amount_minor=p.amount.amount_minor,
                    currency=p.amount.currency,
                    method=p.method.value,
                    paid_on=p.paid_on,
                    reference=p.reference,
                    reference_key=p.reference_key,
                    payer_name=p.payer_name,
                    note=p.note,
                    file_key=p.file.key,
                    file_type=p.file.content_type.value,
                    file_size=p.file.size,
                    file_sha256=p.file.sha256,
                    invoice_id=p.invoice_id,
                    status=p.status.value,
                )
            )
        except IntegrityError as exc:
            text_ = str(exc.orig)
            if "uq_finance_payment_proofs_reference" in text_:
                raise Conflict(
                    "A payment with this reference has already been submitted.",
                    code="duplicate_reference",
                ) from exc
            if "uq_finance_payment_proofs_file" in text_:
                raise Conflict(
                    "This receipt has already been uploaded.", code="duplicate_file"
                ) from exc
            raise

    async def get_proof(self, proof_id: UUID, *, for_update: bool = False) -> PaymentProof | None:
        stmt = select(PaymentProofRow).where(
            PaymentProofRow.tenant_id == self._tid, PaymentProofRow.id == proof_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt.execution_options(populate_existing=True))).scalar()
        return _proof(row) if row else None

    async def save_proof(self, proof: PaymentProof) -> None:
        await self._s.execute(
            update(PaymentProofRow)
            .where(PaymentProofRow.tenant_id == self._tid, PaymentProofRow.id == proof.id)
            .values(
                status=proof.status.value,
                reviewed_by=proof.reviewed_by,
                reviewed_at=proof.reviewed_at,
                review_note=proof.review_note,
                approved_amount_minor=(
                    proof.approved_amount.amount_minor if proof.approved_amount else None
                ),
                payment_id=proof.payment_id,
            )
            .execution_options(synchronize_session=False)
        )

    async def open_proofs_for_student(self, student_id: UUID) -> int:
        return int(
            await self._s.scalar(
                select(func.count()).where(
                    PaymentProofRow.tenant_id == self._tid,
                    PaymentProofRow.student_id == student_id,
                    PaymentProofRow.status == ProofStatus.SUBMITTED.value,
                )
            )
            or 0
        )

    async def list_proofs(
        self,
        *,
        status: ProofStatus | None,
        student_ids: list[UUID] | None,
        cursor: str | None,
        limit: int,
    ) -> Page[PaymentProof]:
        stmt = select(PaymentProofRow).where(PaymentProofRow.tenant_id == self._tid)
        if status is not None:
            stmt = stmt.where(PaymentProofRow.status == status.value)
        if student_ids is not None:
            if not student_ids:
                return Page(items=[], next_cursor=None)
            stmt = stmt.where(PaymentProofRow.student_id.in_(student_ids))
        if cursor:
            stmt = stmt.where(PaymentProofRow.id < _decode(cursor))
        rows = list(
            await self._s.scalars(stmt.order_by(PaymentProofRow.id.desc()).limit(limit + 1))
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_proof(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    # ---- ledger --------------------------------------------------------------------------

    async def post(self, entry: JournalEntry) -> None:
        await self._s.execute(
            pg_insert(JournalEntryRow).values(
                tenant_id=self._tid,
                id=entry.id,
                kind=entry.kind.value,
                source_type=entry.source_type,
                source_id=entry.source_id,
                memo=entry.memo,
                posted_by=entry.posted_by,
                posted_at=entry.posted_at,
            )
        )
        await self._s.execute(
            pg_insert(JournalLineRow).values(
                [
                    {
                        "tenant_id": self._tid,
                        "entry_id": entry.id,
                        "line_no": n,
                        "account": line.account.value,
                        "student_id": line.student_id,
                        "amount_minor": line.amount.amount_minor,
                        "currency": line.amount.currency,
                    }
                    for n, line in enumerate(entry.lines, start=1)
                ]
            )
        )

    async def student_balance(self, student_id: UUID, currency: str) -> StudentBalance:
        rows = await self._s.execute(
            select(JournalLineRow.account, func.coalesce(func.sum(JournalLineRow.amount_minor), 0))
            .where(
                JournalLineRow.tenant_id == self._tid,
                JournalLineRow.student_id == student_id,
                JournalLineRow.currency == currency,
            )
            .group_by(JournalLineRow.account)
        )
        sums = {str(a): int(v) for a, v in rows.all()}
        return StudentBalance(
            owed=Money(sums.get(Account.RECEIVABLE.value, 0), currency),
            # Liabilities carry credit (negative) balances; show them as a positive credit.
            credit=Money(-sums.get(Account.STUDENT_CREDIT.value, 0), currency),
        )

    async def trial_balance(self) -> dict[tuple[Account, str], int]:
        rows = await self._s.execute(
            select(
                JournalLineRow.account,
                JournalLineRow.currency,
                func.sum(JournalLineRow.amount_minor),
            )
            .where(JournalLineRow.tenant_id == self._tid)
            .group_by(JournalLineRow.account, JournalLineRow.currency)
        )
        return {(Account(str(a)), str(c)): int(v) for a, c, v in rows.all()}
