"""Finance persistence contracts. Every method is scoped to the current school."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.finance.billing import Invoice, InvoiceStatus, Scholarship
from app.domain.finance.catalog import FeeItem, FinanceSettings
from app.domain.finance.ledger import Account, JournalEntry, StudentBalance
from app.domain.finance.online import Checkout, GatewayAccount, GatewayProvider
from app.domain.finance.payments import Allocation, Payment, Refund, RefundStatus
from app.domain.finance.proofs import PaymentProof, ProofStatus
from app.domain.finance.spending import Budget, Expense, ExpenseCategory, ExpenseStatus
from app.domain.shared.value_objects import Page


class FinanceRepository(Protocol):
    # settings and numbering
    async def get_settings(self) -> FinanceSettings | None: ...

    async def save_settings(self, settings: FinanceSettings) -> None:
        """Insert or update; raises Conflict if someone saved a newer version first."""
        ...

    async def next_number(self, series: str) -> int:
        """Next value of a per-school counter (invoice, receipt). Gap-free inside a commit."""
        ...

    async def has_invoices(self) -> bool: ...

    async def lock_student(self, student_id: UUID) -> None:
        """Serialise money changes for one student until the transaction ends."""
        ...

    # catalog
    async def add_fee_item(self, item: FeeItem) -> None: ...

    async def get_fee_item(self, item_id: UUID) -> FeeItem | None: ...

    async def save_fee_item(self, item: FeeItem) -> None: ...

    async def list_fee_items(self, *, include_inactive: bool) -> list[FeeItem]: ...

    # scholarships
    async def add_scholarship(self, scholarship: Scholarship) -> None: ...

    async def get_scholarship(self, scholarship_id: UUID) -> Scholarship | None: ...

    async def save_scholarship(self, scholarship: Scholarship) -> None: ...

    async def scholarships_for(
        self, *, student_id: UUID | None, session_id: UUID | None, active_only: bool
    ) -> list[Scholarship]: ...

    # invoices
    async def add_invoices(self, invoices: list[Invoice]) -> None: ...

    async def get_invoice(
        self, invoice_id: UUID, *, for_update: bool = False
    ) -> Invoice | None: ...

    async def save_invoice(self, invoice: Invoice) -> None:
        """Raises Conflict if the invoice changed since it was read."""
        ...

    async def students_billed_for_term(self, term_id: UUID) -> set[UUID]: ...

    async def open_invoices(self, student_id: UUID) -> list[Invoice]:
        """Locked for update."""
        ...

    async def list_invoices(
        self,
        *,
        student_id: UUID | None,
        term_id: UUID | None,
        status: InvoiceStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Invoice]: ...

    async def invoices_of_student(self, student_id: UUID) -> list[Invoice]: ...

    # payments and allocations
    async def add_payment(self, payment: Payment) -> None: ...

    async def get_payment(
        self, payment_id: UUID, *, for_update: bool = False
    ) -> Payment | None: ...

    async def payment_by_key(self, recorded_by: UUID, key: str) -> Payment | None: ...

    async def save_payment(self, payment: Payment) -> None: ...

    async def payments_with_credit(self, student_id: UUID) -> list[Payment]:
        """Recorded payments with unused credit, locked for update."""
        ...

    async def payments_of_student(self, student_id: UUID) -> list[Payment]: ...

    async def list_payments(
        self, *, student_id: UUID | None, cursor: str | None, limit: int
    ) -> Page[Payment]: ...

    async def add_allocations(self, allocations: list[Allocation], now: datetime) -> None: ...

    async def live_allocations(
        self, *, payment_id: UUID | None = None, invoice_id: UUID | None = None
    ) -> list[Allocation]: ...

    async def release_allocations(
        self, *, payment_id: UUID | None = None, invoice_id: UUID | None = None, now: datetime
    ) -> None: ...

    # refunds
    async def add_refund(self, refund: Refund) -> None: ...

    async def get_refund(self, refund_id: UUID, *, for_update: bool = False) -> Refund | None: ...

    async def save_refund(self, refund: Refund) -> None: ...

    async def list_refunds(self, *, status: RefundStatus | None, limit: int) -> list[Refund]: ...

    async def refunds_of_student(self, student_id: UUID) -> list[Refund]: ...

    async def payment_reference_exists(self, reference_key: str) -> bool:
        """A live payment already carries this bank reference (normalised)."""
        ...

    # proofs of payment
    async def add_proof(self, proof: PaymentProof) -> None:
        """Raises Conflict when an open or approved proof has the same reference or file."""
        ...

    async def get_proof(
        self, proof_id: UUID, *, for_update: bool = False
    ) -> PaymentProof | None: ...

    async def save_proof(self, proof: PaymentProof) -> None: ...

    async def open_proofs_for_student(self, student_id: UUID) -> int: ...

    async def list_proofs(
        self,
        *,
        status: ProofStatus | None,
        student_ids: list[UUID] | None,
        cursor: str | None,
        limit: int,
    ) -> Page[PaymentProof]: ...

    # ledger
    async def post(self, entry: JournalEntry) -> None: ...

    async def student_balance(self, student_id: UUID, currency: str) -> StudentBalance: ...

    async def trial_balance(self) -> dict[tuple[Account, str], int]:
        """Sum of each account per currency. Always sums to zero per currency."""
        ...


class OnlinePaymentRepository(Protocol):
    """A school's provider connection and its checkouts. Scoped to the current school, except
    ``pending_checkouts``, which reconciliation runs across schools in platform scope."""

    async def get_gateway(
        self, provider: GatewayProvider | None = None, *, include_inactive: bool = False
    ) -> GatewayAccount | None:
        """The active connection (for ``provider``, or whichever is active). With
        ``include_inactive``, also a disconnected one, to confirm payments made before."""
        ...

    async def save_gateway(self, account: GatewayAccount) -> None:
        """Insert or replace the connection for its provider; other providers are deactivated."""
        ...

    async def deactivate_gateways(self) -> int: ...

    async def add_checkout(self, checkout: Checkout) -> None: ...

    async def get_checkout(
        self, checkout_id: UUID, *, for_update: bool = False
    ) -> Checkout | None: ...

    async def get_checkout_by_reference(
        self, reference: str, *, for_update: bool = False
    ) -> Checkout | None: ...

    async def save_checkout(self, checkout: Checkout) -> None: ...

    async def recent_checkout(self, payer_id: UUID, student_id: UUID) -> Checkout | None:
        """The payer's newest checkout for this student, if any."""
        ...

    async def list_checkouts(
        self, *, student_ids: list[UUID] | None, cursor: str | None, limit: int
    ) -> Page[Checkout]: ...

    async def pending_checkouts(self, *, older_than: datetime, limit: int) -> list[Checkout]:
        """Oldest pending checkouts created before ``older_than``, across every school in
        platform scope."""
        ...


class SpendingRepository(Protocol):
    """Expense categories, budgets and expenses of the school in scope."""

    async def add_category(self, category: ExpenseCategory) -> None: ...

    async def get_category(self, category_id: UUID) -> ExpenseCategory | None: ...

    async def save_category(self, category: ExpenseCategory) -> None: ...

    async def list_categories(self, *, include_inactive: bool) -> list[ExpenseCategory]: ...

    async def category_name_exists(self, name: str, *, other_than: UUID | None = None) -> bool:
        """Case-insensitive."""
        ...

    async def get_budget(
        self, term_id: UUID, category_id: UUID, *, for_update: bool = False
    ) -> Budget | None: ...

    async def save_budget(self, budget: Budget) -> None:
        """Insert or update (one budget per term and category)."""
        ...

    async def budgets_for_term(self, term_id: UUID) -> list[Budget]: ...

    async def add_expense(self, expense: Expense) -> None: ...

    async def get_expense(
        self, expense_id: UUID, *, for_update: bool = False
    ) -> Expense | None: ...

    async def save_expense(self, expense: Expense) -> None: ...

    async def list_expenses(
        self,
        *,
        term_id: UUID | None,
        category_id: UUID | None,
        status: ExpenseStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Expense]: ...

    async def spend_by_category(self, term_id: UUID, currency: str) -> dict[UUID, tuple[int, int]]:
        """category id -> (approved minor, pending minor) for the term."""
        ...

    async def lock_category_term(self, term_id: UUID, category_id: UUID) -> None:
        """Serialise approvals against one budget (transaction-scoped advisory lock)."""
        ...
