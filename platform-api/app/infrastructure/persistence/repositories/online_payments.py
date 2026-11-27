"""Payment provider connections and online checkouts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.finance.online import (
    Checkout,
    CheckoutStatus,
    GatewayAccount,
    GatewayProvider,
    KeyMode,
)
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.finance import CheckoutRow, GatewayAccountRow
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _account(r: GatewayAccountRow) -> GatewayAccount:
    return GatewayAccount(
        id=r.id,
        tenant_id=r.tenant_id,
        provider=GatewayProvider(r.provider),
        public_key=r.public_key,
        secret_key_ciphertext=r.secret_key_ciphertext,
        key_hint=r.key_hint,
        mode=KeyMode(r.mode),
        connected_by=r.connected_by,
        connected_at=r.connected_at,
        active=r.active,
    )


def _checkout(r: CheckoutRow) -> Checkout:
    return Checkout(
        id=r.id,
        tenant_id=r.tenant_id,
        reference=r.reference,
        provider=GatewayProvider(r.provider),
        student_id=r.student_id,
        payer_id=r.payer_id,
        payer_email=r.payer_email,
        amount=Money(r.amount_minor, r.currency),
        created_at=r.created_at,
        expires_at=r.expires_at,
        status=CheckoutStatus(r.status),
        authorization_url=r.authorization_url,
        provider_transaction_id=r.provider_transaction_id,
        channel=r.channel,
        paid_amount=(
            Money(r.paid_amount_minor, r.currency) if r.paid_amount_minor is not None else None
        ),
        payment_id=r.payment_id,
        failure_reason=r.failure_reason,
        completed_at=r.completed_at,
    )


def _values(c: Checkout) -> dict[str, object]:
    return {
        "status": c.status.value,
        "authorization_url": c.authorization_url,
        "provider_transaction_id": c.provider_transaction_id,
        "channel": c.channel,
        "paid_amount_minor": c.paid_amount.amount_minor if c.paid_amount else None,
        "payment_id": c.payment_id,
        "failure_reason": c.failure_reason,
        "completed_at": c.completed_at,
    }


class SqlOnlinePaymentRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Online payments need a tenant scope.")
        return self._scope.tenant_id

    # ---- provider connection -------------------------------------------------------------

    async def get_gateway(
        self, provider: GatewayProvider | None = None, *, include_inactive: bool = False
    ) -> GatewayAccount | None:
        stmt = select(GatewayAccountRow).where(GatewayAccountRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(GatewayAccountRow.active.is_(True))
        if provider is not None:
            stmt = stmt.where(GatewayAccountRow.provider == provider.value)
        row = await self._s.scalar(
            stmt.order_by(
                GatewayAccountRow.active.desc(), GatewayAccountRow.connected_at.desc()
            ).limit(1)
        )
        return _account(row) if row else None

    async def save_gateway(self, account: GatewayAccount) -> None:
        a = account
        await self._s.execute(
            update(GatewayAccountRow)
            .where(
                GatewayAccountRow.tenant_id == self._tid,
                GatewayAccountRow.provider != a.provider.value,
            )
            .values(active=False)
        )
        values = {
            "id": a.id,
            "public_key": a.public_key,
            "secret_key_ciphertext": a.secret_key_ciphertext,
            "key_hint": a.key_hint,
            "mode": a.mode.value,
            "connected_by": a.connected_by,
            "connected_at": a.connected_at,
            "active": a.active,
        }
        await self._s.execute(
            pg_insert(GatewayAccountRow)
            .values(tenant_id=self._tid, provider=a.provider.value, **values)
            .on_conflict_do_update(index_elements=["tenant_id", "provider"], set_=values)
        )

    async def deactivate_gateways(self) -> int:
        result = await self._s.execute(
            update(GatewayAccountRow)
            .where(GatewayAccountRow.tenant_id == self._tid, GatewayAccountRow.active.is_(True))
            .values(active=False)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # ---- checkouts -----------------------------------------------------------------------

    async def add_checkout(self, checkout: Checkout) -> None:
        c = checkout
        await self._s.execute(
            pg_insert(CheckoutRow).values(
                tenant_id=self._tid,
                id=c.id,
                reference=c.reference,
                provider=c.provider.value,
                student_id=c.student_id,
                payer_id=c.payer_id,
                payer_email=c.payer_email,
                amount_minor=c.amount.amount_minor,
                currency=c.amount.currency,
                created_at=c.created_at,
                expires_at=c.expires_at,
                **_values(c),
            )
        )

    async def get_checkout(self, checkout_id: UUID, *, for_update: bool = False) -> Checkout | None:
        stmt = select(CheckoutRow).where(
            CheckoutRow.tenant_id == self._tid, CheckoutRow.id == checkout_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _checkout(row) if row else None

    async def get_checkout_by_reference(
        self, reference: str, *, for_update: bool = False
    ) -> Checkout | None:
        stmt = select(CheckoutRow).where(
            CheckoutRow.tenant_id == self._tid, CheckoutRow.reference == reference
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _checkout(row) if row else None

    async def save_checkout(self, checkout: Checkout) -> None:
        await self._s.execute(
            update(CheckoutRow)
            .where(CheckoutRow.tenant_id == checkout.tenant_id, CheckoutRow.id == checkout.id)
            .values(**_values(checkout))
        )

    async def recent_checkout(self, payer_id: UUID, student_id: UUID) -> Checkout | None:
        row = await self._s.scalar(
            select(CheckoutRow)
            .where(
                CheckoutRow.tenant_id == self._tid,
                CheckoutRow.payer_id == payer_id,
                CheckoutRow.student_id == student_id,
            )
            .order_by(CheckoutRow.created_at.desc())
            .limit(1)
        )
        return _checkout(row) if row else None

    async def list_checkouts(
        self, *, student_ids: list[UUID] | None, cursor: str | None, limit: int
    ) -> Page[Checkout]:
        stmt = select(CheckoutRow).where(CheckoutRow.tenant_id == self._tid)
        if student_ids is not None:
            if not student_ids:
                return Page(items=[], next_cursor=None)
            stmt = stmt.where(CheckoutRow.student_id.in_(student_ids))
        if cursor:
            stmt = stmt.where(CheckoutRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(CheckoutRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_checkout(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def pending_checkouts(self, *, older_than: datetime, limit: int) -> list[Checkout]:
        if not self._scope.platform:
            raise RuntimeError("Reconciliation runs in platform scope.")
        rows = await self._s.scalars(
            select(CheckoutRow)
            .where(CheckoutRow.status == "pending", CheckoutRow.created_at < older_than)
            .order_by(CheckoutRow.created_at)
            .limit(limit)
        )
        return [_checkout(r) for r in rows]
