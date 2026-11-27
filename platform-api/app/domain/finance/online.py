"""Paying school fees online through a payment provider (Paystack first).

The school is the merchant: it connects its own provider account, and money settles straight to
the school's bank. The platform never holds school fees.

A checkout is one attempt to pay. It starts ``pending`` when the parent is sent to the provider's
payment page, and only becomes ``succeeded`` after the provider's API confirms the charge, either
from a signed webhook or when the parent returns. The webhook body alone is never trusted. Success
records an ordinary ``online`` payment, so ledger, credit, receipts and statements work as for any
other payment. Each checkout can produce at most one payment.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot, Entity
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money

#: A checkout nobody finished is closed after this long.
CHECKOUT_LIFETIME = timedelta(hours=24)
#: A pending checkout for the same student and amount is reused within this window, so a
#: double-click doesn't open two payment pages.
CHECKOUT_REUSE_WINDOW = timedelta(minutes=30)
_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class GatewayProvider(StrEnum):
    PAYSTACK = "paystack"


def parse_provider(value: str) -> GatewayProvider:
    try:
        return GatewayProvider(value.strip().lower())
    except ValueError as exc:
        raise ValidationFailed(
            "That payment provider isn't supported yet.", details={"field": "provider"}
        ) from exc


class KeyMode(StrEnum):
    TEST = "test"
    LIVE = "live"


@dataclass(kw_only=True, eq=False)
class GatewayAccount(Entity):
    """A school's connection to its own payment provider account."""

    tenant_id: UUID
    provider: GatewayProvider
    public_key: str
    secret_key_ciphertext: str
    key_hint: str
    mode: KeyMode
    connected_by: UUID
    connected_at: datetime
    active: bool = True

    @classmethod
    def connect(
        cls,
        *,
        tenant_id: UUID,
        provider: GatewayProvider,
        public_key: str,
        secret_key: str,
        secret_key_ciphertext: str,
        mode: KeyMode,
        by: UUID,
        now: datetime,
    ) -> GatewayAccount:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            provider=provider,
            public_key=public_key.strip(),
            secret_key_ciphertext=secret_key_ciphertext,
            key_hint=secret_key.strip()[-4:],
            mode=mode,
            connected_by=by,
            connected_at=now,
        )


class CheckoutStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    #: Money was taken but doesn't match the checkout (wrong currency). A person must look.
    NEEDS_REVIEW = "needs_review"


FINAL_STATUSES = frozenset(
    {
        CheckoutStatus.SUCCEEDED,
        CheckoutStatus.FAILED,
        CheckoutStatus.ABANDONED,
        CheckoutStatus.NEEDS_REVIEW,
    }
)


def new_reference() -> str:
    """Unique, unguessable, and accepted by every provider (letters, digits and dashes)."""
    body = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(20))
    return f"MAS-{body}"


@dataclass(frozen=True, kw_only=True)
class OnlinePaymentReceived(DomainEvent):
    name: ClassVar[str] = "finance.online_payment_received"
    checkout_id: UUID
    payment_id: UUID
    student_id: UUID
    payer_id: UUID
    amount_minor: int
    currency: str


@dataclass(kw_only=True, eq=False)
class Checkout(AggregateRoot):
    tenant_id: UUID
    reference: str
    provider: GatewayProvider
    student_id: UUID
    payer_id: UUID
    payer_email: str
    amount: Money
    created_at: datetime
    expires_at: datetime
    status: CheckoutStatus = CheckoutStatus.PENDING
    authorization_url: str | None = None
    provider_transaction_id: str | None = None
    channel: str | None = None
    paid_amount: Money | None = None
    payment_id: UUID | None = None
    failure_reason: str | None = None
    completed_at: datetime | None = None

    @classmethod
    def start(
        cls,
        *,
        tenant_id: UUID,
        provider: GatewayProvider,
        student_id: UUID,
        payer_id: UUID,
        payer_email: str,
        amount: Money,
        now: datetime,
    ) -> Checkout:
        if amount.amount_minor <= 0:
            raise ValidationFailed(
                "Enter an amount greater than zero.", details={"field": "amount_minor"}
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            reference=new_reference(),
            provider=provider,
            student_id=student_id,
            payer_id=payer_id,
            payer_email=payer_email,
            amount=amount,
            created_at=now,
            expires_at=now + CHECKOUT_LIFETIME,
        )

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    def can_reuse(self, *, student_id: UUID, amount: Money, now: datetime) -> bool:
        return (
            self.status is CheckoutStatus.PENDING
            and self.authorization_url is not None
            and self.student_id == student_id
            and self.amount == amount
            and now - self.created_at < CHECKOUT_REUSE_WINDOW
        )

    def open(self, authorization_url: str) -> None:
        if self.status is not CheckoutStatus.PENDING:
            raise InvalidStateTransition("This payment is already closed.")
        self.authorization_url = authorization_url

    def succeed(
        self,
        *,
        paid: Money,
        payment_id: UUID,
        provider_transaction_id: str | None,
        channel: str | None,
        now: datetime,
    ) -> None:
        """The provider confirmed the charge. The amount actually paid is what gets credited:
        the money is in the school's account either way."""
        if self.status is CheckoutStatus.SUCCEEDED:
            return
        if self.status is not CheckoutStatus.PENDING and self.status is not (
            CheckoutStatus.ABANDONED
        ):
            raise InvalidStateTransition("This payment is already closed.")
        if paid.currency != self.amount.currency:
            raise ValidationFailed("The provider charged a different currency.")
        self.status = CheckoutStatus.SUCCEEDED
        self.paid_amount = paid
        self.payment_id = payment_id
        self.provider_transaction_id = provider_transaction_id
        self.channel = channel
        self.completed_at = now
        self.failure_reason = None
        self.record(
            OnlinePaymentReceived(
                tenant_id=self.tenant_id,
                checkout_id=self.id,
                payment_id=payment_id,
                student_id=self.student_id,
                payer_id=self.payer_id,
                amount_minor=paid.amount_minor,
                currency=paid.currency,
            )
        )

    def flag_for_review(
        self, reason: str, *, provider_transaction_id: str | None, now: datetime
    ) -> None:
        if self.status is CheckoutStatus.SUCCEEDED:
            return
        self.status = CheckoutStatus.NEEDS_REVIEW
        self.failure_reason = reason[:200]
        self.provider_transaction_id = provider_transaction_id
        self.completed_at = now

    def fail(self, reason: str, *, now: datetime) -> None:
        if self.is_final:
            return
        self.status = CheckoutStatus.FAILED
        self.failure_reason = reason[:200]
        self.completed_at = now

    def abandon(self, *, now: datetime) -> None:
        """Closed unpaid. A late success from the provider can still complete it."""
        if self.is_final:
            return
        self.status = CheckoutStatus.ABANDONED
        self.completed_at = now
