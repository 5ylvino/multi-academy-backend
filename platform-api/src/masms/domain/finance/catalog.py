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


@dataclass(kw_only=True, eq=False)
class FinanceSettings(Entity):
    """One row per school. ``id`` equals ``tenant_id``."""

    tenant_id: UUID
    currency: str
    default_due_days: int = 14
    version: int = 0

    @classmethod
    def defaults(cls, tenant_id: UUID, currency: str) -> FinanceSettings:
        return cls(id=tenant_id, tenant_id=tenant_id, currency=currency_code(currency))

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
