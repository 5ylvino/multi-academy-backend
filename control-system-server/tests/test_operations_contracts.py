from datetime import datetime, timedelta, timezone

import pytest

from app.models import TenantOffboardJob, TenantProvisioningJob
from app.services.domain_provisioning import validate_hostname_kind
from app.services.offboarding import dispatch_due_wipes
from app.services.payment_adapters import build_split_amounts, validate_provider_adapter
from app.services.provisioning import apply_initialization_result, queue_initialization


def test_wipe_waits_for_export_ready(session, tenant):
    job = TenantOffboardJob(
        tenant_id=tenant.id,
        status="export_pending",
        wipe_scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    session.add(job)
    session.commit()

    assert dispatch_due_wipes(session) == []
    session.refresh(job)
    assert job.status == "export_pending"


def test_split_execution_preserves_total_minor_units():
    amounts = build_split_amounts(
        101,
        [
            {"destination": "school", "percentage_bps": 9500},
            {"destination": "platform", "percentage_bps": 500},
        ],
    )
    assert [row["amount_minor"] for row in amounts] == [95, 6]
    assert sum(row["amount_minor"] for row in amounts) == 101


def test_provider_and_domain_ports_exclude_unsupported_paths():
    with pytest.raises(ValueError, match="KIRA"):
        validate_provider_adapter("kira")
    with pytest.raises(ValueError):
        validate_hostname_kind("school", "custom")


def test_initialization_request_is_explicit_and_not_complete(session, tenant):
    job, created = queue_initialization(
        session,
        tenant=tenant,
        requested_by="operator@example.com",
        requested_by_id="7",
        idempotency_key="init-1",
    )
    session.commit()

    assert created is True
    assert job.status == "queued"
    assert job.school_db_status == "pending"
    assert tenant.status == "provisioning"
    assert session.query(TenantProvisioningJob).count() == 1

    replay, created = queue_initialization(
        session,
        tenant=tenant,
        requested_by="operator@example.com",
        requested_by_id="7",
        idempotency_key="init-1",
    )
    assert created is False
    assert replay.id == job.id


def test_initialization_only_activates_after_school_db_ready(session, tenant):
    job, _ = queue_initialization(
        session,
        tenant=tenant,
        requested_by="operator@example.com",
        requested_by_id="7",
        idempotency_key="init-2",
    )
    apply_initialization_result(
        session,
        tenant=tenant,
        job=job,
        status="running",
        school_db_status="initializing",
    )
    assert tenant.status == "provisioning"

    apply_initialization_result(
        session,
        tenant=tenant,
        job=job,
        status="succeeded",
        school_db_status="ready",
    )
    assert tenant.status == "active"
