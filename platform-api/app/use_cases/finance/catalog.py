"""Finance settings, the fee catalog and scholarships."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.academics.structure import SchoolLevelCode, TermCode
from app.domain.finance.billing import Scholarship, ScholarshipKind
from app.domain.finance.catalog import BankAccount, FeeItem, FinanceSettings
from app.domain.identity.roles import Realm
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money
from app.use_cases.finance.common import (
    MoneyView,
    check_currency,
    load_settings,
    require_student,
)
from app.use_cases.shared.ports import AuditEntry, Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class BankAccountView:
    bank_name: str
    account_name: str
    account_number: str


@dataclass(frozen=True, slots=True)
class SettingsView:
    currency: str
    default_due_days: int
    currency_locked: bool
    online_payments_enabled: bool
    payment_proofs_enabled: bool
    payment_instructions: str | None
    bank_accounts: list[BankAccountView]

    @classmethod
    def of(cls, s: FinanceSettings, *, locked: bool) -> SettingsView:
        return cls(
            currency=s.currency,
            default_due_days=s.default_due_days,
            currency_locked=locked,
            online_payments_enabled=s.online_payments_enabled,
            payment_proofs_enabled=s.payment_proofs_enabled,
            payment_instructions=s.payment_instructions,
            bank_accounts=[
                BankAccountView(a.bank_name, a.account_name, a.account_number)
                for a in s.bank_accounts
            ],
        )


@dataclass(frozen=True, slots=True)
class UpdateSettingsCommand:
    currency: str
    default_due_days: int
    online_payments_enabled: bool = False
    payment_proofs_enabled: bool = True
    payment_instructions: str | None = None
    bank_accounts: list[BankAccountView] | None = None


@dataclass(frozen=True, slots=True)
class FeeItemCommand:
    name: str
    amount_minor: int
    currency: str | None
    school_level: str | None
    class_id: UUID | None
    terms: list[str]
    mandatory: bool


@dataclass(frozen=True, slots=True)
class FeeItemView:
    id: UUID
    name: str
    amount: MoneyView
    school_level: str | None
    class_id: UUID | None
    terms: list[str]
    mandatory: bool
    active: bool
    created_at: datetime

    @classmethod
    def of(cls, item: FeeItem) -> FeeItemView:
        return cls(
            id=item.id,
            name=item.name,
            amount=MoneyView.of(item.amount),
            school_level=item.school_level,
            class_id=item.class_id,
            terms=sorted(item.terms),
            mandatory=item.mandatory,
            active=item.active,
            created_at=item.created_at,
        )


@dataclass(frozen=True, slots=True)
class ScholarshipCommand:
    student_id: UUID
    session_id: UUID | None
    name: str
    kind: str
    value: int


@dataclass(frozen=True, slots=True)
class ScholarshipView:
    id: UUID
    student_id: UUID
    session_id: UUID
    name: str
    kind: str
    value: int
    currency: str
    active: bool
    granted_by: UUID
    granted_at: datetime
    revoked_at: datetime | None

    @classmethod
    def of(cls, s: Scholarship) -> ScholarshipView:
        return cls(
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
            revoked_at=s.revoked_at,
        )


#: No payment gateway is wired yet (next slice), so online payments can't be switched on.
GATEWAY_READY = False


def _level(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return SchoolLevelCode(value).value
    except ValueError as exc:
        raise ValidationFailed("Unknown school level.", details={"field": "school_level"}) from exc


def _terms(values: list[str]) -> frozenset[str]:
    try:
        return frozenset(TermCode(v).value for v in values)
    except ValueError as exc:
        raise ValidationFailed("Unknown term.", details={"field": "terms"}) from exc


class FinanceCatalog:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    # ---- settings ------------------------------------------------------------------------

    async def get_settings(self, actor: Principal) -> SettingsView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:view")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            settings = await load_settings(uow, tenant_id)
            locked = await uow.finance.has_invoices()
        return SettingsView.of(settings, locked=locked)

    async def update_settings(self, actor: Principal, cmd: UpdateSettingsCommand) -> SettingsView:
        """Currency, due days and how parents may pay. Online payments stay off until a
        payment gateway is connected; proof of outside payment can be switched off."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await load_settings(uow, tenant_id)
            locked = await uow.finance.has_invoices()
            before = SettingsView.of(settings, locked=locked)
            settings.update(
                currency=cmd.currency,
                default_due_days=cmd.default_due_days,
                currency_locked=locked,
            )
            settings.set_payment_options(
                online_payments_enabled=cmd.online_payments_enabled,
                payment_proofs_enabled=cmd.payment_proofs_enabled,
                payment_instructions=cmd.payment_instructions,
                bank_accounts=[
                    BankAccount(a.bank_name, a.account_name, a.account_number)
                    for a in (cmd.bank_accounts or [])
                ],
                gateway_ready=GATEWAY_READY,
            )
            after = SettingsView.of(settings, locked=locked)
            await uow.finance.save_settings(settings)
            uow.audit(
                AuditEntry(
                    action="finance.settings_updated",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="finance_settings",
                    target_id=str(tenant_id),
                    metadata={
                        key: {"from": getattr(before, key), "to": getattr(after, key)}
                        for key in (
                            "currency",
                            "default_due_days",
                            "online_payments_enabled",
                            "payment_proofs_enabled",
                        )
                        if getattr(before, key) != getattr(after, key)
                    },
                )
            )
            await uow.commit()
        return after

    # ---- fee items -----------------------------------------------------------------------

    async def _check_class(self, uow: UnitOfWork, class_id: UUID | None) -> None:
        if class_id is not None and await uow.academics.get_class(class_id) is None:
            raise NotFound("Class not found.")

    def _amount(self, settings: FinanceSettings, cmd: FeeItemCommand) -> Money:
        return Money.positive(cmd.amount_minor, check_currency(settings, cmd.currency))

    async def list_fee_items(
        self, actor: Principal, *, include_inactive: bool
    ) -> list[FeeItemView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("fee_structures:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            items = await uow.finance.list_fee_items(include_inactive=include_inactive)
        return [FeeItemView.of(i) for i in items]

    async def create_fee_item(self, actor: Principal, cmd: FeeItemCommand) -> FeeItemView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("fee_structures:create")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await load_settings(uow, tenant_id)
            await self._check_class(uow, cmd.class_id)
            item = FeeItem.create(
                tenant_id=tenant_id,
                name=cmd.name,
                amount=self._amount(settings, cmd),
                school_level=_level(cmd.school_level),
                class_id=cmd.class_id,
                terms=_terms(cmd.terms),
                mandatory=cmd.mandatory,
                now=self._clock.now(),
            )
            await uow.finance.add_fee_item(item)
            self._audit(uow, actor, "finance.fee_item_created", item)
            await uow.commit()
        return FeeItemView.of(item)

    async def update_fee_item(
        self, actor: Principal, item_id: UUID, cmd: FeeItemCommand
    ) -> FeeItemView:
        """Changes apply to future invoices only; issued invoices keep their amounts."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fee_structures:update")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            item = await uow.finance.get_fee_item(item_id)
            if item is None:
                raise NotFound("Fee item not found.")
            settings = await load_settings(uow, tenant_id)
            await self._check_class(uow, cmd.class_id)
            item.update(
                name=cmd.name,
                amount=self._amount(settings, cmd),
                school_level=_level(cmd.school_level),
                class_id=cmd.class_id,
                terms=_terms(cmd.terms),
                mandatory=cmd.mandatory,
            )
            await uow.finance.save_fee_item(item)
            self._audit(uow, actor, "finance.fee_item_updated", item)
            await uow.commit()
        return FeeItemView.of(item)

    async def set_fee_item_active(
        self, actor: Principal, item_id: UUID, *, active: bool
    ) -> FeeItemView:
        """Fee items are retired, never deleted, because issued invoices refer to them."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fee_structures:delete" if not active else "fee_structures:update")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            item = await uow.finance.get_fee_item(item_id)
            if item is None:
                raise NotFound("Fee item not found.")
            item.active = active
            await uow.finance.save_fee_item(item)
            self._audit(
                uow,
                actor,
                "finance.fee_item_activated" if active else "finance.fee_item_retired",
                item,
            )
            await uow.commit()
        return FeeItemView.of(item)

    def _audit(self, uow: UnitOfWork, actor: Principal, action: str, item: FeeItem) -> None:
        uow.audit(
            AuditEntry(
                action=action,
                actor_id=actor.user_id,
                actor_realm=Realm.SCHOOL,
                tenant_id=item.tenant_id,
                target_type="fee_item",
                target_id=str(item.id),
                metadata={
                    "name": item.name,
                    "amount_minor": item.amount.amount_minor,
                    "currency": item.amount.currency,
                },
            )
        )

    # ---- scholarships --------------------------------------------------------------------

    async def list_scholarships(
        self, actor: Principal, *, student_id: UUID | None, active_only: bool
    ) -> list[ScholarshipView]:
        actor.require_realm(Realm.SCHOOL)
        if not (
            actor.permissions.allows("scholarships:manage")
            or actor.permissions.allows("invoices:read")
        ):
            raise PermissionDenied("You do not have permission to do this.")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            found = await uow.finance.scholarships_for(
                student_id=student_id, session_id=None, active_only=active_only
            )
        return [ScholarshipView.of(s) for s in found]

    async def grant_scholarship(self, actor: Principal, cmd: ScholarshipCommand) -> ScholarshipView:
        """Applies to term invoices issued after the grant. Issued invoices are not changed."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("scholarships:manage")
        try:
            kind = ScholarshipKind(cmd.kind)
        except ValueError as exc:
            raise ValidationFailed(
                "Kind must be percent or fixed.", details={"field": "kind"}
            ) from exc
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await require_student(uow, tenant_id, cmd.student_id)
            session_id = cmd.session_id
            if session_id is None:
                term = await uow.academics.current_term()
                if term is None:
                    raise ValidationFailed("Set the current term first.", code="no_current_term")
                session_id = term.session_id
            elif await uow.academics.get_session(session_id) is None:
                raise NotFound("Session not found.")
            settings = await load_settings(uow, tenant_id)
            scholarship = Scholarship.grant(
                tenant_id=tenant_id,
                student_id=cmd.student_id,
                session_id=session_id,
                name=cmd.name,
                kind=kind,
                value=cmd.value,
                currency=settings.currency,
                by=actor.user_id,
                now=self._clock.now(),
            )
            await uow.finance.add_scholarship(scholarship)
            uow.audit(
                AuditEntry(
                    action="finance.scholarship_granted",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="scholarship",
                    target_id=str(scholarship.id),
                    metadata={
                        "student_id": str(cmd.student_id),
                        "kind": kind.value,
                        "value": cmd.value,
                    },
                )
            )
            await uow.commit()
        return ScholarshipView.of(scholarship)

    async def revoke_scholarship(self, actor: Principal, scholarship_id: UUID) -> ScholarshipView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("scholarships:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            scholarship = await uow.finance.get_scholarship(scholarship_id)
            if scholarship is None:
                raise NotFound("Scholarship not found.")
            scholarship.revoke(by=actor.user_id, now=self._clock.now())
            await uow.finance.save_scholarship(scholarship)
            uow.audit(
                AuditEntry(
                    action="finance.scholarship_revoked",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="scholarship",
                    target_id=str(scholarship.id),
                    metadata={"student_id": str(scholarship.student_id)},
                )
            )
            await uow.commit()
        return ScholarshipView.of(scholarship)
