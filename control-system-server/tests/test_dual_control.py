"""Dual-control coverage: which actions require a second approver."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.dual_control import DUAL_ACTIONS
from app.services import offboarding


class TestActionCoverage:
    @pytest.mark.parametrize(
        "action",
        [
            "tenant.blacklist",
            "tenant.unblacklist",
            "tenant.destroy",
            "tenant.offboard",
            "flag.kill_switch",
            "flag.kill_switch_lift",
            "provider.switch_live",
            "provider.switch_from_live",
        ],
    )
    def test_high_risk_actions_are_registered(self, action):
        assert action in DUAL_ACTIONS

    def test_lifting_a_kill_switch_is_gated(self):
        """Only *setting* a kill switch was gated, so one operator could quietly
        re-enable a feature that had been killed during an incident."""
        assert "flag.kill_switch_lift" in DUAL_ACTIONS

    def test_demoting_a_live_provider_is_gated(self):
        """Only switches *to* live were gated, so moving live payments back to
        sandbox needed no second approver."""
        assert "provider.switch_from_live" in DUAL_ACTIONS

    def test_offboarding_is_gated(self):
        """Offboarding schedules irreversible data destruction but previously
        required only `tenants:write`, despite the console advertising it as
        dual-controlled."""
        assert "tenant.offboard" in DUAL_ACTIONS


class TestOffboardingLifecycle:
    def test_scheduling_marks_tenant_and_emits_export_request(self, session, tenant):
        from app.models import OutboxEvent

        job = offboarding.schedule_offboard_job(
            session, tenant=tenant, wipe_in_days=30, created_by="ops@example.com"
        )
        session.commit()

        assert job.status == "export_pending"
        assert job.wipe_scheduled_at is not None
        assert tenant.status == "archived"
        assert tenant.status_reason == "offboarding"

        events = [
            e.event_type
            for e in session.query(OutboxEvent).all()
        ]
        assert "tenant.offboard.export_requested" in events

    def test_scheduling_twice_reuses_the_active_job(self, session, tenant):
        first = offboarding.schedule_offboard_job(session, tenant=tenant)
        session.commit()
        second = offboarding.schedule_offboard_job(session, tenant=tenant)
        session.commit()
        assert first.id == second.id

    def test_due_wipe_is_dispatched(self, session, tenant):
        """The job row used to be written and never read, so the wipe date passed
        silently and tenant data was retained indefinitely."""
        from app.models import OutboxEvent

        job = offboarding.schedule_offboard_job(session, tenant=tenant, wipe_in_days=30)
        job.status = "wipe_scheduled"  # school-server export acknowledgement
        job.wipe_scheduled_at = datetime.now(timezone.utc) - timedelta(days=1)
        session.commit()

        results = offboarding.dispatch_due_wipes(session)
        session.commit()

        assert [r["outcome"] for r in results] == ["wipe_dispatched"]
        assert job.status == "wipe_dispatched"
        assert "tenant.offboard.wipe_requested" in [
            e.event_type for e in session.query(OutboxEvent).all()
        ]

    def test_future_wipe_is_not_dispatched(self, session, tenant):
        offboarding.schedule_offboard_job(session, tenant=tenant, wipe_in_days=30)
        session.commit()
        assert offboarding.dispatch_due_wipes(session) == []

    def test_legal_hold_applied_after_scheduling_blocks_the_wipe(self, session, tenant):
        """Legal hold is re-checked at dispatch, not only at scheduling time."""
        job = offboarding.schedule_offboard_job(session, tenant=tenant, wipe_in_days=30)
        job.status = "wipe_scheduled"  # school-server export acknowledgement
        job.wipe_scheduled_at = datetime.now(timezone.utc) - timedelta(days=1)
        tenant.legal_hold = True
        tenant.legal_hold_reason = "Pending litigation"
        session.commit()

        results = offboarding.dispatch_due_wipes(session)
        session.commit()

        assert [r["outcome"] for r in results] == ["blocked_legal_hold"]
        assert job.status == "blocked_legal_hold"
        assert job.legal_hold_blocked is True

    def test_cancelling_restores_the_tenant(self, session, tenant):
        job = offboarding.schedule_offboard_job(session, tenant=tenant)
        session.commit()
        assert tenant.status == "archived"

        offboarding.cancel_offboard_job(
            session, job, reason="Customer renewed", staff_email="ops@example.com"
        )
        session.commit()

        assert job.status == "cancelled"
        assert tenant.status == "active"
        assert tenant.status_reason == ""

    def test_cancelling_does_not_revive_a_tenant_suspended_for_other_reasons(
        self, session, tenant
    ):
        job = offboarding.schedule_offboard_job(session, tenant=tenant)
        tenant.status = "suspended"
        tenant.status_reason = "abuse"
        session.commit()

        offboarding.cancel_offboard_job(
            session, job, reason="mistake", staff_email="ops@example.com"
        )
        session.commit()
        assert tenant.status == "suspended"

    def test_completion_is_recorded(self, session, tenant):
        job = offboarding.schedule_offboard_job(session, tenant=tenant)
        session.commit()
        offboarding.complete_offboard_job(session, job, ok=True, detail="12 tables dropped")
        session.commit()
        assert job.status == "completed"

    def test_failure_is_recorded(self, session, tenant):
        job = offboarding.schedule_offboard_job(session, tenant=tenant)
        session.commit()
        offboarding.complete_offboard_job(session, job, ok=False, detail="permission denied")
        session.commit()
        assert job.status == "failed"
