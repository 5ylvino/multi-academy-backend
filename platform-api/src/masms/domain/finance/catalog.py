"""The fee catalog and a school's finance settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from masms.domain.shared.entity import Entity
from masms.domain.shared.errors import ValidationFailed
from masms.domain.shared.ids import new_id
from masms.domain.shared.money import Money, currency_code

MAX_DUE_DAYS = 180

#: Kept as plain codes so finance stays independent of the academics feature. The use cases
#: check them against the academics enums.
SCHOOL_LEVELS = frozenset({"nursery", "primary", "secondary"})
TERM_CODES = frozenset({"first", "second", "third"})


def _name(value: str, field_name: str = "name", max_len: int = 120) -> str:
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= max_len:
        raise ValidationFailed(
            f"Enter a name of up to {max_len} characters.", details={"field": field_name}
        )
    return clean


MAX_BANK_ACCOUNTS = 5
MAX_INSTRUCTIONS = 1000


@dataclass(frozen=True, slots=True)
class BankAccount:
    """Where parents pay school fees directly. Shown to parents beside their invoices."""

    bank_name: str
    account_name: str
    account_number: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "bank_name", _name(self.bank_name, "bank_name", 80))
        object.__setattr__(self, "account_name", _name(self.account_name, "account_name", 120))
        number = "".join(self.account_number.split())
        if not 4 <= len(number) <= 34 or not number.isalnum():
            raise ValidationFailed(
                "Enter a valid account number.", details={"field": "account_number"}
            )
        object.__setattr__(self, "account_number", number)


@dataclass(kw_only=True, eq=False)
class FinanceSettings(Entity):
    """One row per school. ``id`` equals ``tenant_id``.

    How parents may pay is the school's choice. Many schools collect fees outside the platform
    (bank transfer, cash at the bursary), so online payments are off until the school turns them
    on, while uploading proof of an outside payment is on by default.
    """

    tenant_id: UUID
    currency: str
    default_due_days: int = 14
    online_payments_enabled: bool = False
    payment_proofs_enabled: bool = True
    payment_instructions: str | None = None
    bank_accounts: tuple[BankAccount, ...] = ()
    version: int = 0

    @classmethod
    def defaults(cls, tenant_id: UUID, currency: str) -> FinanceSettings:
        return cls(id=tenant_id, tenant_id=tenant_id, currency=currency_code(currency))

    def set_payment_options(
        self,
        *,
        online_payments_enabled: bool,
        payment_proofs_enabled: bool,
        payment_instructions: str | None,
        bank_accounts: list[BankAccount],
        gateway_ready: bool,
    ) -> None:
        if online_payments_enabled and not gateway_ready:
            raise ValidationFailed(
                "Connect a payment gateway before turning on online payments.",
                code="gateway_not_configured",
                details={"field": "online_payments_enabled"},
            )
        if len(bank_accounts) > MAX_BANK_ACCOUNTS:
            raise ValidationFailed(
                f"List at most {MAX_BANK_ACCOUNTS} bank accounts.",
                details={"field": "bank_accounts"},
            )
        text = (payment_instructions or "").strip()
        if len(text) > MAX_INSTRUCTIONS:
            raise ValidationFailed(
                "Keep the payment instructions under 1000 characters.",
                details={"field": "payment_instructions"},
            )
        self.online_payments_enabled = online_payments_enabled
        self.payment_proofs_enabled = payment_proofs_enabled
        self.payment_instructions = text or None
        self.bank_accounts = tuple(bank_accounts)

    def update(self, *, currency: str, default_due_days: int, currency_locked: bool) -> None:
        code = currency_code(currency)
        if code != self.currency and currency_locked:
            raise ValidationFailed(
                "The currency can't change once invoices exist.",
                code="currency_locked",
                details={"field": "currency"},
            )
        if not 0 <= default_due_days <= MAX_DUE_DAYS:
            raise ValidationFailed(
                "Pick between 0 and 180 days.", details={"field": "default_due_days"}
            )
        self.currency = code
        self.default_due_days = default_due_days


@dataclass(kw_only=True, eq=False)
class FeeItem(Entity):
    """A charge in the catalog, e.g. "Tuition" or "Bus fare".

    Scope: every class, or one school level, or one class. ``terms`` empty means every term.
    Mandatory items are billed by term billing runs; optional ones are added per student.
    """

    tenant_id: UUID
    name: str
    amount: Money
    school_level: str | None = None
    class_id: UUID | None = None
    terms: frozenset[str] = field(default_factory=frozenset)
    mandatory: bool = True
    active: bool = True
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        amount: Money,
        school_level: str | None,
        class_id: UUID | None,
        terms: frozenset[str],
        mandatory: bool,
        now: datetime,
    ) -> FeeItem:
        item = cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name),
            amount=amount,
            created_at=now,
        )
        item.update(
            name=name,
            amount=amount,
            school_level=school_level,
            class_id=class_id,
            terms=terms,
            mandatory=mandatory,
        )
        return item

    def update(
        self,
        *,
        name: str,
        amount: Money,
        school_level: str | None,
        class_id: UUID | None,
        terms: frozenset[str],
        mandatory: bool,
    ) -> None:
        if amount.amount_minor <= 0:
            raise ValidationFailed(
                "Enter an amount greater than zero.", details={"field": "amount"}
            )
        if school_level is not None and school_level not in SCHOOL_LEVELS:
            raise ValidationFailed("Unknown school level.", details={"field": "school_level"})
        if not terms <= TERM_CODES:
            raise ValidationFailed("Unknown term.", details={"field": "terms"})
        if school_level is not None and class_id is not None:
            raise ValidationFailed(
                "Apply a fee to a level or to one class, not both.", details={"field": "class_id"}
            )
        self.name = _name(name)
        self.amount = amount
        self.school_level = school_level
        self.class_id = class_id
        self.terms = terms
        self.mandatory = mandatory

    def applies_to(self, *, class_id: UUID, school_level: str, term: str) -> bool:
        if not self.active:
            return False
        if self.terms and term not in self.terms:
            return False
        if self.class_id is not None:
            return self.class_id == class_id
        if self.school_level is not None:
            return self.school_level == school_level
        return True
