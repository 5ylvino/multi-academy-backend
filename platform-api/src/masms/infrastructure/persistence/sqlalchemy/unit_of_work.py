"""SQLAlchemy unit of work with row-level-security scoping and a transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from masms.domain.shared.entity import AggregateRoot
from masms.domain.shared.events import DomainEvent
from masms.domain.shared.ids import new_id
from masms.infrastructure.persistence.models.system import AuditEventRow, OutboxEventRow
from masms.infrastructure.persistence.repositories.identity import (
    SqlRefreshSessionRepository,
    SqlUserRepository,
)
from masms.infrastructure.persistence.repositories.platform import (
    SqlFeatureFlagRepository,
    SqlPlanRepository,
)
from masms.infrastructure.persistence.repositories.scope_state import ScopeState
from masms.infrastructure.persistence.repositories.tenancy import (
    SqlEmailVerificationRepository,
    SqlTenantRepository,
)
from masms.use_cases.shared.ports import AuditEntry, Scope


class SqlAlchemyUnitOfWork:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], scope: Scope, *, read_only: bool
    ) -> None:
        self._factory = session_factory
        self._initial_scope = scope
        self._read_only = read_only
        self._scope = ScopeState()
        self._tracked: list[AggregateRoot] = []
        self._events: list[DomainEvent] = []
        self._audit: list[AuditEntry] = []
        self._committed = False
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("Unit of work is not active.")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        await self._session.begin()
        if self._read_only:
            await self._session.execute(text("SET TRANSACTION READ ONLY"))
        if self._initial_scope.platform:
            await self._set("app.rls_bypass", "on")
            self._scope.platform = True
        if self._initial_scope.tenant_id is not None:
            await self.enter_tenant(self._initial_scope.tenant_id)

        s = self._session
        self.users = SqlUserRepository(s, self._scope)
        self.sessions = SqlRefreshSessionRepository(s)
        self.tenants = SqlTenantRepository(s)
        self.verifications = SqlEmailVerificationRepository(s)
        self.flags = SqlFeatureFlagRepository(s)
        self.plans = SqlPlanRepository(s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def enter_tenant(self, tenant_id: UUID) -> None:
        # SET LOCAL semantics (is_local = true): the setting ends with the transaction, which keeps
        # it safe behind PgBouncer in transaction-pooling mode.
        await self._set("app.tenant_id", str(tenant_id))
        self._scope.tenant_id = tenant_id

    def track(self, aggregate: AggregateRoot) -> None:
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    def publish(self, event: DomainEvent) -> None:
        self._events.append(event)

    def audit(self, entry: AuditEntry) -> None:
        self._audit.append(entry)

    async def commit(self) -> None:
        if self._read_only:
            raise RuntimeError("Read-only unit of work cannot commit.")
        session = self.session
        events = list(self._events)
        for aggregate in self._tracked:
            events.extend(aggregate.pull_events())
        now = datetime.now(UTC)
        for event in events:
            session.add(
                OutboxEventRow(
                    id=event.event_id,
                    name=event.name,
                    tenant_id=event.tenant_id,
                    payload=event.payload(),
                    sensitive_fields=sorted(event.sensitive_fields),
                    occurred_at=event.occurred_at,
                    available_at=now,
                )
            )
        for entry in self._audit:
            session.add(
                AuditEventRow(
                    id=new_id(),
                    tenant_id=entry.tenant_id,
                    actor_id=entry.actor_id,
                    actor_realm=entry.actor_realm.value if entry.actor_realm else None,
                    action=entry.action,
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    metadata_=_json_safe(dict(entry.metadata)),
                    created_at=now,
                )
            )
        await session.commit()
        self._committed = True
        self._events.clear()
        self._audit.clear()
        self._tracked.clear()

    async def rollback(self) -> None:
        if self._session is not None and self._session.in_transaction():
            await self._session.rollback()
        for aggregate in self._tracked:
            aggregate.pull_events()

    async def _set(self, name: str, value: str) -> None:
        await self.session.execute(select(func.set_config(name, value, True)))


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


class SqlAlchemyUnitOfWorkFactory:
    """Routes read-only work to a replica when one is configured."""

    def __init__(
        self,
        primary: async_sessionmaker[AsyncSession],
        replica: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._primary = primary
        self._replica = replica

    def __call__(self, scope: Scope, *, read_only: bool = False) -> SqlAlchemyUnitOfWork:
        factory = self._replica if read_only and self._replica is not None else self._primary
        return SqlAlchemyUnitOfWork(factory, scope, read_only=read_only)
