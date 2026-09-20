"""Billing correctness: period arithmetic, catch-up invoicing, partial payments.

These cover the defects that were losing or mis-stating revenue.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import billing


def _dt(y, m, d, **kw):
    return datetime(y, m, d, tzinfo=timezone.utc, **kw)


class TestMonthArithmetic:
    def test_end_of_month_clamps_to_shorter_month(self):
        assert billing._add_months(_dt(2026, 1, 31), 1).date().isoformat() == "2026-02-28"

    def test_leap_year_february(self):
        assert billing._add_months(_dt(2028, 1, 31), 1).date().isoformat() == "2028-02-29"

    def test_anchor_day_does_not_drift_across_short_months(self):
        """The bug: timedelta(days=30) walked the billing date backwards every
        cycle and billed 12.17 times a year."""
        anchor = _dt(2026, 1, 31)
        current = anchor
        for _ in range(12):
            current = billing._add_months(current, 1, anchor_day=31)
        assert current.date().isoformat() == "2027-01-31"

    def test_year_rollover(self):
        assert billing._add_months(_dt(2026, 11, 15), 3).date().isoformat() == "2027-02-15"

    @pytest.mark.parametrize(
        "cycle,months", [("monthly", 1), ("termly", 3), ("yearly", 12)]
    )
    def test_cycle_months(self, cycle, months):
        assert billing._cycle_months(cycle) == months

    def test_unknown_cycle_defaults_to_monthly(self):
        assert billing.normalize_cycle("fortnightly") == "monthly"
        assert billing.normalize_cycle(None) == "monthly"
        assert billing.normalize_cycle("  YEARLY ") == "yearly"


class TestInvoiceGeneration:
    def test_number_is_deterministic_not_timestamp_based(self):
        """Two invoices generated in the same second used to collide on the
        unique `number` column and abort the batch."""
        a = billing._invoice_number(7, "2026-07", 3)
        b = billing._invoice_number(7, "2026-07", 3)
        c = billing._invoice_number(7, "2026-07", 4)
        assert a == b
        assert a != c

    def test_period_key_granularity(self, session, subscription):
        subscription.billing_cycle = "monthly"
        assert billing.period_key_for(subscription, _dt(2026, 7, 15)) == "2026-07"
        subscription.billing_cycle = "yearly"
        assert billing.period_key_for(subscription, _dt(2026, 7, 15)) == "2026"

    def test_period_window_follows_billing_anchor(self, session, subscription):
        start, end = billing.period_bounds(subscription, _dt(2026, 7, 15))
        assert start.date().isoformat() == "2026-07-15"
        # Ends the instant before the next period starts, not at month end.
        assert end.date().isoformat() == "2026-08-14"

    def test_second_invoice_for_same_period_is_refused(self, session, subscription):
        first = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 7, 1)
        )
        session.commit()
        assert first is not None

        again = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 7, 1)
        )
        assert again is None, "the same billing period must not be invoiced twice"

    def test_catch_up_bills_every_missed_period(self, session, subscription, tenant):
        """A subscription 3 months overdue must produce 3 invoices. The old code
        emitted one and jumped next_invoice_at forward, forgiving the rest."""
        from app.models import SaasInvoice

        subscription.next_invoice_at = _dt(2026, 1, 15)
        session.commit()

        # Freeze "now" four months after the first missed period.
        real_now = billing._now
        billing._now = lambda: _dt(2026, 4, 20)
        try:
            created = billing.generate_due_invoices(session)
        finally:
            billing._now = real_now

        invoices = session.query(SaasInvoice).all()
        assert len(created) == 4, f"expected Jan–Apr, got {[i.period_key for i in invoices]}"
        assert sorted(i.period_key for i in invoices) == [
            "2026-01",
            "2026-02",
            "2026-03",
            "2026-04",
        ]
        # And the schedule has moved past the frozen now. SQLite returns naive
        # datetimes, so normalise before comparing.
        nia = subscription.next_invoice_at
        if nia.tzinfo is None:
            nia = nia.replace(tzinfo=timezone.utc)
        assert nia > _dt(2026, 4, 20)

    def test_catch_up_is_bounded(self, session, subscription):
        """A subscription years behind must stop at the ceiling instead of
        emitting an unbounded run of invoices."""
        from app.models import SaasInvoice

        subscription.next_invoice_at = _dt(2000, 1, 15)
        session.commit()

        real_now = billing._now
        billing._now = lambda: _dt(2026, 4, 20)
        try:
            billing.generate_due_invoices(session)
        finally:
            billing._now = real_now

        assert session.query(SaasInvoice).count() == billing.MAX_CATCHUP_PERIODS

    def test_future_subscriptions_are_not_invoiced(self, session, subscription):
        from app.models import SaasInvoice

        real_now = billing._now
        billing._now = lambda: _dt(2026, 1, 1)
        try:
            created = billing.generate_due_invoices(session)
        finally:
            billing._now = real_now
        assert created == []
        assert session.query(SaasInvoice).count() == 0


class TestPartialPayments:
    def _invoice(self, session, subscription, amount=500_000):
        inv = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 7, 1)
        )
        inv.amount_minor = amount
        session.commit()
        return inv

    def test_underpayment_does_not_settle_invoice(self, session, subscription):
        """The headline bug: any payment of any size marked the invoice paid in
        full, so ₦1 closed a ₦5,000 invoice."""
        inv = self._invoice(session, subscription)
        result = billing.apply_payment_to_invoice(session, inv, 100)
        session.commit()

        assert inv.status == "partially_paid"
        assert inv.paid_at is None
        assert result["outstandingMinor"] == 499_900
        assert result["settled"] is False

    def test_instalments_settle_once_fully_covered(self, session, subscription):
        inv = self._invoice(session, subscription)
        billing.apply_payment_to_invoice(session, inv, 200_000)
        assert inv.status == "partially_paid"
        billing.apply_payment_to_invoice(session, inv, 300_000)
        session.commit()

        assert inv.status == "paid"
        assert inv.paid_at is not None
        assert inv.balance_minor == 0

    def test_overpayment_is_reported(self, session, subscription):
        inv = self._invoice(session, subscription)
        result = billing.apply_payment_to_invoice(session, inv, 600_000)
        session.commit()
        assert inv.status == "paid"
        assert result["overpaidMinor"] == 100_000

    def test_zero_and_negative_amounts_rejected(self, session, subscription):
        inv = self._invoice(session, subscription)
        for bad in (0, -500):
            with pytest.raises(ValueError):
                billing.apply_payment_to_invoice(session, inv, bad)

    def test_partial_payment_does_not_unsuspend_tenant(self, session, subscription, tenant):
        """Un-suspending on a token payment let a school back in without paying."""
        tenant.status = "suspended"
        tenant.status_reason = "billing_dunning"
        inv = self._invoice(session, subscription)
        session.commit()

        billing.apply_payment_to_invoice(session, inv, 1)
        session.commit()
        assert tenant.status == "suspended"

        billing.apply_payment_to_invoice(session, inv, 499_999)
        session.commit()
        assert tenant.status == "active"
        assert tenant.status_reason == ""

    def test_settling_one_invoice_leaves_tenant_suspended_when_another_is_open(
        self, session, subscription, tenant
    ):
        tenant.status = "suspended"
        tenant.status_reason = "billing_dunning"
        first = self._invoice(session, subscription)

        second = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 8, 1)
        )
        second.amount_minor = 500_000
        session.commit()

        billing.apply_payment_to_invoice(session, first, 500_000)
        session.commit()

        assert first.status == "paid"
        assert tenant.status == "suspended", "one unpaid invoice still remains"

    def test_reactivation_keys_off_status_reason_not_message_text(
        self, session, subscription, tenant
    ):
        """Reactivation used to require the literal substring "saas" in the
        human-readable status message, so rewording the copy broke it."""
        tenant.status = "suspended"
        tenant.status_reason = "billing_dunning"
        tenant.status_message = "Account paused — please settle your balance."
        inv = self._invoice(session, subscription)
        session.commit()

        billing.apply_payment_to_invoice(session, inv, 500_000)
        session.commit()
        assert tenant.status == "active"

    def test_non_billing_suspension_is_not_lifted_by_payment(
        self, session, subscription, tenant
    ):
        tenant.status = "suspended"
        tenant.status_reason = "abuse"
        inv = self._invoice(session, subscription)
        session.commit()

        billing.apply_payment_to_invoice(session, inv, 500_000)
        session.commit()
        assert tenant.status == "suspended", "paying a bill must not clear an abuse block"


class TestCourtesyUnlockExpiry:
    def _invoice(self, session, subscription, amount=500_000):
        inv = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 7, 1)
        )
        inv.amount_minor = amount
        session.commit()
        return inv

    def test_expired_unlock_re_suspends_billing_dunning_tenant(
        self, session, subscription, tenant
    ):
        self._invoice(session, subscription)
        tenant.status = "active"
        tenant.status_reason = "billing_dunning"
        tenant.status_message = "Courtesy unlock until tomorrow"
        tenant.courtesy_unlock_until = _dt(2026, 1, 1, hour=12)
        session.commit()

        expired = billing.run_courtesy_unlock_expiry_pass(session)
        session.commit()

        assert expired == 1
        assert tenant.status == "suspended"
        assert tenant.status_reason == "billing_dunning"
        assert tenant.courtesy_unlock_until is None

    def test_expired_unlock_re_suspends_grace_expired_tenant(self, session, tenant):
        tenant.status = "active"
        tenant.status_reason = "billing_grace_expired"
        tenant.courtesy_unlock_until = _dt(2026, 1, 1)
        session.commit()

        expired = billing.run_courtesy_unlock_expiry_pass(session)
        session.commit()

        assert expired == 1
        assert tenant.status == "suspended"
        assert tenant.status_reason == "billing_grace_expired"

    def test_expired_unlock_clears_when_billing_resolved(
        self, session, subscription, tenant
    ):
        tenant.status = "active"
        tenant.status_reason = "billing_dunning"
        tenant.courtesy_unlock_until = _dt(2026, 1, 1)
        subscription.status = "active"
        subscription.dunning_step = "none"
        inv = billing.create_invoice_for_subscription(
            session, subscription=subscription, period_start=_dt(2026, 7, 1)
        )
        billing.settle_invoice(session, inv)
        session.commit()

        expired = billing.run_courtesy_unlock_expiry_pass(session)
        session.commit()

        assert expired == 0
        assert tenant.status == "active"
        assert tenant.courtesy_unlock_until is None
        assert tenant.status_reason == ""

    def test_future_unlock_is_left_alone(self, session, tenant):
        tenant.status = "active"
        tenant.status_reason = "billing_dunning"
        tenant.courtesy_unlock_until = _dt(2099, 1, 1)
        session.commit()

        expired = billing.run_courtesy_unlock_expiry_pass(session)
        session.commit()

        assert expired == 0
        assert tenant.status == "active"
        assert tenant.courtesy_unlock_until is not None


class TestMrr:
    def test_yearly_normalised_to_monthly(self, session, subscription):
        subscription.billing_cycle = "yearly"
        subscription.price_minor = 1_200_000
        session.commit()
        assert billing.monthly_recurring_minor(subscription, session) == 100_000

    def test_rounds_to_nearest_rather_than_flooring(self, session, subscription):
        subscription.billing_cycle = "termly"
        subscription.price_minor = 100
        session.commit()
        # 100 / 3 = 33.33 → 33 either way, but 101/3 = 33.67 should round up.
        subscription.price_minor = 101
        assert billing.monthly_recurring_minor(subscription, session) == 34

    def test_per_student_pricing_multiplies(self, session, subscription):
        subscription.pricing_model = "per_student"
        subscription.per_student_minor = 50_000
        subscription.student_count = 300
        session.commit()
        assert billing.resolve_period_amount_minor(subscription, session) == 15_000_000
