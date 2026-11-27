"""Declarative base with a stable naming convention, so Alembic diffs stay deterministic."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: Tables protected by row-level security on ``tenant_id``. Migrations enable RLS on each,
#: and a test asserts that every table with a ``tenant_id`` column is either here or allow-listed.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "school_users",
    "email_verifications",
    "user_invites",
    "school_guardian_links",
    "academic_sessions",
    "academic_terms",
    "academic_classes",
    "academic_subjects",
    "academic_class_subjects",
    "academic_enrolments",
    "attendance_settings",
    "attendance_student_marks",
    "attendance_staff_check_ins",
    "finance_settings",
    "finance_counters",
    "finance_fee_items",
    "finance_scholarships",
    "finance_invoices",
    "finance_payments",
    "finance_allocations",
    "finance_refunds",
    "finance_journal_entries",
    "finance_journal_lines",
    "finance_payment_proofs",
    "finance_gateway_accounts",
    "finance_checkouts",
    "finance_expense_categories",
    "finance_budgets",
    "finance_expenses",
    "inventory_items",
    "inventory_movements",
    "payroll_settings",
    "payroll_profiles",
    "payroll_runs",
    "payroll_payslips",
    "communication_announcements",
    "communication_announcement_reads",
    "communication_conversations",
    "communication_participants",
    "communication_messages",
    "communication_notifications",
    "safeguarding_concerns",
    "safeguarding_entries",
    "platform_subscriptions",
    "platform_invoices",
    "platform_invoice_checkouts",
)

#: Tables that carry ``tenant_id`` but are registry or platform data, read before a tenant is known.
TENANT_REGISTRY_TABLES: tuple[str, ...] = (
    "auth_sessions",
    "tenant_flag_overrides",
    "outbox_events",
    "audit_events",
)
