"""Platform billing persistence. Runs in platform scope, except reads a school makes of its own
subscription and invoices, which RLS limits to that school."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.shared.value_objects import Page
from app.domain.subscriptions.billing import InvoiceStatus, PlatformInvoice, Subscription


class SubscriptionRepository(Protocol):
    async def get(self, tenant_id: UUID, *, for_update: bool = False) -> Subscription | None: ...

    async def save(self, subscription: Subscription) -> None:
        """Insert or update."""
        ...

    async def schools_without_subscription(self, *, limit: int) -> list[UUID]:
        """Serving schools that predate billing or slipped through; the cycle adds them."""
        ...

    async def due_for_invoice(self, *, before: datetime, limit: int) -> list[Subscription]:
        """Subscriptions of serving schools whose next period starts before ``before`` and has
        no invoice yet (a void invoice doesn't count, so voiding lets the cycle issue anew)."""
        ...

    async def next_period_invoice(
        self, tenant_id: UUID, *, after: datetime
    ) -> PlatformInvoice | None:
        """A non-void invoice for a period ending after ``after`` (the next period's invoice)."""
        ...

    async def count_active_students(self, tenant_id: UUID) -> int: ...

    async def next_invoice_number(self) -> int: ...

    async def add_invoice(self, invoice: PlatformInvoice) -> None: ...

    async def get_invoice(
        self, invoice_id: UUID, *, for_update: bool = False
    ) -> PlatformInvoice | None: ...

    async def get_invoice_by_reference(
        self, reference: str, *, for_update: bool = False
    ) -> PlatformInvoice | None:
        """Any checkout reference ever started for the invoice, not only the latest."""
        ...

    async def record_checkout(
        self, *, reference: str, tenant_id: UUID, invoice_id: UUID, now: datetime
    ) -> None: ...

    async def save_invoice(self, invoice: PlatformInvoice) -> None: ...

    async def list_invoices(
        self,
        *,
        tenant_id: UUID | None,
        status: InvoiceStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[PlatformInvoice]: ...

    async def reminders_due(self, *, now: datetime, limit: int) -> list[PlatformInvoice]:
        """Open invoices past due, within the reminder window, not reminded recently."""
        ...

    async def invoices_to_enforce(self, *, now: datetime, limit: int) -> list[PlatformInvoice]:
        """Open invoices past their grace period whose school is still serving."""
        ...

    async def invoices_awaiting_payment(
        self, *, started_before: datetime, limit: int
    ) -> list[PlatformInvoice]:
        """Open invoices with an online checkout started before ``started_before``."""
        ...
