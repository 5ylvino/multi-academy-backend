from datetime import datetime, timedelta, timezone

from app.services.trials import (
    ensure_install,
    initialize_trial_once,
    install_matches,
    run_trial_pass,
    trial_metadata,
)


def test_install_token_is_stored_as_hash_and_is_device_bound(session, tenant):
    install, token = ensure_install(db=session, tenant=tenant, device_hash="device-1")
    session.commit()
    assert token
    assert install.install_token_hash != token
    assert install_matches(install, token, "device-1")
    assert not install_matches(install, token, "device-2")


def test_trial_initializes_once_and_expires_with_outbox(session, tenant):
    trial = initialize_trial_once(
        session, tenant=tenant, started_at=datetime.now(timezone.utc) - timedelta(days=91)
    )
    assert initialize_trial_once(session, tenant=tenant).id == trial.id
    session.commit()
    result = run_trial_pass(session)
    session.commit()
    session.refresh(trial)
    session.refresh(tenant)
    assert result["expired"] == 1
    assert trial.status == "expired"
    assert tenant.status == "restricted"
    assert trial_metadata(session, tenant)["expired"] is True
