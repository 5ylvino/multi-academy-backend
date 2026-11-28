"""Repository ports for tenancy."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.shared.value_objects import Page, Slug
from app.domain.tenancy.tenant import Tenant, TenantStatus
from app.domain.tenancy.verification import EmailVerification


class TenantRepository(Protocol):
    async def get(self, tenant_id: UUID) -> Tenant | None: ...

    async def get_by_slug(self, slug: Slug) -> Tenant | None: ...

    async def slug_exists(self, slug: Slug) -> bool: ...

    async def add(self, tenant: Tenant) -> None: ...

    async def save(self, tenant: Tenant) -> None: ...

    async def list(
        self, *, status: TenantStatus | None, search: str | None, cursor: str | None, limit: int
    ) -> Page[Tenant]: ...


class EmailVerificationRepository(Protocol):
    async def latest_for_user(self, tenant_id: UUID, user_id: UUID) -> EmailVerification | None: ...

    async def add(self, verification: EmailVerification) -> None: ...

    async def save(self, verification: EmailVerification) -> None: ...
