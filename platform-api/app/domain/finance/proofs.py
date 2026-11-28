"""Proof of payment made outside the platform.

A parent pays the school directly (bank transfer, deposit, POS) and uploads the receipt. Nothing
is credited until the bursar checks the money arrived and approves it; approval records an
ordinary payment, so the ledger, credit and statements work as for any other payment. The person
who approves must not be the person who submitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.finance.billing import clean_reason
from app.domain.finance.payments import PaymentMethod
from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import (
    InvalidStateTransition,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money

MAX_FILE_BYTES = 5 * 1024 * 1024
MIN_FILE_BYTES = 100
MAX_OPEN_PER_STUDENT = 10
MAX_PAID_DAYS_AGO = 365

PROOF_METHODS = frozenset({PaymentMethod.BANK_TRANSFER, PaymentMethod.POS, PaymentMethod.CHEQUE})


class ReceiptType(StrEnum):
    PDF = "application/pdf"
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"

    @property
    def extension(self) -> str:
        return {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}.get(
            self.value, "webp"
        )


def detect_receipt_type(data: bytes) -> ReceiptType:
    """Decide the type from the file's own bytes, never from its name or the client's claim."""
    if len(data) < MIN_FILE_BYTES:
        raise ValidationFailed("That file is empty or too small.", details={"field": "file"})
    if len(data) > MAX_FILE_BYTES:
        raise ValidationFailed("Upload a file of up to 5 MB.", details={"field": "file"})
    if data.startswith(b"%PDF-"):
        return ReceiptType.PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ReceiptType.PNG
    if data.startswith(b"\xff\xd8\xff"):
        return ReceiptType.JPEG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ReceiptType.WEBP
    raise ValidationFailed(
        "Upload a PDF, PNG, JPEG or WebP file.", code="unsupported_file", details={"field": "file"}
    )


_REF_KEEP = re.compile(r"[^A-Z0-9]")


def normalise_reference(reference: str) -> str:
    """Bank references are compared without spaces, dashes or case, to catch re-submissions."""
    return _REF_KEEP.sub("", reference.upper())


class ProofStatus(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class ProofFile:
    key: str
    content_type: ReceiptType
    size: int
    sha256: str


@dataclass(frozen=True, kw_only=True)
class ProofSubmitted(DomainEvent):
    name: ClassVar[str] = "finance.payment_proof_submitted"
    proof_id: UUID
    student_id: UUID
    amount_minor: int
    currency: str


@dataclass(frozen=True, kw_only=True)
class ProofReviewed(DomainEvent):
    name: ClassVar[str] = "finance.payment_proof_reviewed"
    proof_id: UUID
    student_id: UUID
    submitted_by: UUID
    status: str
    payment_id: str | None


@dataclass(kw_only=True, eq=False)
class PaymentProof(AggregateRoot):
    tenant_id: UUID
    student_id: UUID
    submitted_by: UUID
    submitted_at: datetime
    amount: Money
    method: PaymentMethod
    paid_on: date
    reference: str
    reference_key: str
    payer_name: str | None
    note: str | None
    file: ProofFile
    invoice_id: UUID | None = None
    status: ProofStatus = ProofStatus.SUBMITTED
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    approved_amount: Money | None = None
    payment_id: UUID | None = None

    @staticmethod
    def file_key(tenant_id: UUID, proof_id: UUID, kind: ReceiptType) -> str:
        return f"tenants/{tenant_id}/payment-proofs/{proof_id}.{kind.extension}"

    @classmethod
    def submit(
        cls,
        *,
        proof_id: UUID | None = None,
        tenant_id: UUID,
        student_id: UUID,
        by: UUID,
        amount: Money,
        method: PaymentMethod,
        paid_on: date,
        reference: str,
        payer_name: str | None,
        note: str | None,
        file: ProofFile,
        invoice_id: UUID | None,
        today: date,
        now: datetime,
    ) -> PaymentProof:
        if method not in PROOF_METHODS:
            raise ValidationFailed(
                "Choose bank transfer, POS or cheque.", details={"field": "method"}
            )
        if amount.amount_minor <= 0:
            raise ValidationFailed("Enter the amount you paid.", details={"field": "amount"})
        if paid_on > today:
            raise ValidationFailed(
                "The payment date can't be in the future.", details={"field": "paid_on"}
            )
        if (today - paid_on).days > MAX_PAID_DAYS_AGO:
            raise ValidationFailed(
                "That payment is more than a year old. Contact the school.",
                details={"field": "paid_on"},
            )
        ref = " ".join(reference.split())
        key = normalise_reference(ref)
        if not len(key) >= 4 or len(ref) > 120:
            raise ValidationFailed(
                "Enter the transaction reference from your bank receipt.",
                details={"field": "reference"},
            )
        proof = cls(
            id=proof_id or new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            submitted_by=by,
            submitted_at=now,
            amount=amount,
            method=method,
            paid_on=paid_on,
            reference=ref,
            reference_key=key,
            payer_name=" ".join((payer_name or "").split())[:120] or None,
            note=" ".join((note or "").split())[:500] or None,
            file=file,
            invoice_id=invoice_id,
        )
        proof.record(
            ProofSubmitted(
                tenant_id=tenant_id,
                proof_id=proof.id,
                student_id=student_id,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
            )
        )
        return proof

    def _review(self, by: UUID, now: datetime) -> None:
        if self.status is not ProofStatus.SUBMITTED:
            raise InvalidStateTransition("This proof of payment has already been dealt with.")
        if by == self.submitted_by:
            raise PermissionDenied(
                "Someone else must review a proof of payment you uploaded.",
                code="dual_control_required",
            )
        self.reviewed_by = by
        self.reviewed_at = now

    def approve(
        self,
        *,
        by: UUID,
        now: datetime,
        payment_id: UUID,
        amount: Money | None,
        note: str | None,
    ) -> Money:
        """Returns the amount to record. A different amount than claimed needs a note."""
        received = amount or self.amount
        if received.amount_minor <= 0:
            raise ValidationFailed("Enter the amount received.", details={"field": "amount"})
        clean = " ".join((note or "").split())[:500] or None
        if received != self.amount and clean is None:
            raise ValidationFailed(
                "Say why the amount received differs from the amount claimed.",
                details={"field": "note"},
            )
        self._review(by, now)
        self.status = ProofStatus.APPROVED
        self.approved_amount = received
        self.payment_id = payment_id
        self.review_note = clean
        self._announce()
        return received

    def reject(self, *, by: UUID, now: datetime, reason: str) -> None:
        clean = clean_reason(reason)
        self._review(by, now)
        self.status = ProofStatus.REJECTED
        self.review_note = clean
        self._announce()

    def withdraw(self, *, by: UUID) -> None:
        if self.status is not ProofStatus.SUBMITTED:
            raise InvalidStateTransition("Only a proof waiting for review can be withdrawn.")
        if by != self.submitted_by:
            raise PermissionDenied("Only the person who uploaded it can withdraw it.")
        self.status = ProofStatus.WITHDRAWN

    def _announce(self) -> None:
        self.record(
            ProofReviewed(
                tenant_id=self.tenant_id,
                proof_id=self.id,
                student_id=self.student_id,
                submitted_by=self.submitted_by,
                status=self.status.value,
                payment_id=str(self.payment_id) if self.payment_id else None,
            )
        )
