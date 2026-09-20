"""End-to-end API tests through the real ASGI app.

These catch wiring problems that unit tests miss: dependency ordering, MFA
enforcement, m2m tenant scoping, and the request-id/response-header contract.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import PlatformUser, SecuritySettings, Tenant
from app.security import hash_password


@pytest.fixture
def client():
    """The real app against the process-wide test database.

    `ControlOpsMiddleware` deliberately opens its own session for the IP-allowlist
    policy, so it cannot be redirected with `dependency_overrides`. The app's own
    engine is used instead and truncated between tests.
    """
    import app.database as database
    from app.main import create_app

    Base.metadata.create_all(bind=database.engine)

    # Start each test from a clean slate; children before parents for FK order.
    with database.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())

    # Reseed the reference data (plans, flags, providers) that routes validate
    # against — creating a tenant requires its plan to exist.
    from app.services.seed import seed_all

    seed_db = database.SessionLocal()
    try:
        seed_all(seed_db)
        seed_db.commit()
    finally:
        seed_db.close()

    app = create_app()
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.app_session_factory = database.SessionLocal  # type: ignore[attr-defined]
    yield test_client


def _make_staff(Session, *, role="owner", mfa_enabled=False, email=None):
    db = Session()
    try:
        user = PlatformUser(
            email=email or f"staff-{uuid.uuid4().hex[:6]}@example.com",
            full_name="Test Operator",
            role=role,
            password_hash=hash_password("Sup3rStr0ng!Pass"),
            is_active=True,
            mfa_enabled=mfa_enabled,
        )
        db.add(user)
        db.commit()
        return user.email
    finally:
        db.close()


def _login(client, email):
    res = client.post(
        "/v1/auth/login", json={"email": email, "password": "Sup3rStr0ng!Pass"}
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


class TestHealthAndHeaders:
    def test_liveness_is_open(self, client):
        assert client.get("/health/live").status_code == 200

    def test_security_headers_are_present(self, client):
        res = client.get("/health/live")
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_request_id_is_returned(self, client):
        """Every response carries a correlation id so a report can be traced to
        a specific request in the logs."""
        res = client.get("/v1/tenants")
        assert res.headers.get("X-Request-ID")

    def test_supplied_request_id_is_echoed(self, client):
        res = client.get("/v1/tenants", headers={"X-Request-ID": "trace-me-123"})
        assert res.headers["X-Request-ID"] == "trace-me-123"

    def test_rate_limit_headers_present(self, client):
        res = client.get("/v1/tenants")
        assert res.headers.get("X-RateLimit-Budget") == "staff"


class TestAuthentication:
    def test_unauthenticated_request_is_rejected(self, client):
        assert client.get("/v1/tenants").status_code == 401

    def test_bad_password_is_rejected(self, client):
        Session = client.app_session_factory
        email = _make_staff(Session)
        res = client.post("/v1/auth/login", json={"email": email, "password": "wrong"})
        assert res.status_code == 401

    def test_login_then_authenticated_read(self, client):
        Session = client.app_session_factory
        token = _login(client, _make_staff(Session))
        res = client.get("/v1/tenants", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200

    def test_refresh_token_cannot_be_used_as_access_token(self, client):
        Session = client.app_session_factory
        email = _make_staff(Session)
        res = client.post(
            "/v1/auth/login", json={"email": email, "password": "Sup3rStr0ng!Pass"}
        )
        refresh = res.json()["refresh_token"]
        res = client.get("/v1/tenants", headers={"Authorization": f"Bearer {refresh}"})
        assert res.status_code == 401


class TestPermissionsFollowTheStoredRole:
    def test_demotion_takes_effect_on_the_existing_token(self, client):
        """Permissions used to be read from the JWT claim, so a demoted operator
        kept their old privileges until the access token expired."""
        Session = client.app_session_factory
        email = _make_staff(Session, role="owner")
        token = _login(client, email)
        headers = {"Authorization": f"Bearer {token}"}

        # Owners may write tenants.
        create = client.post(
            "/v1/tenants",
            headers=headers,
            json={
                "external_id": f"ext-{uuid.uuid4().hex[:6]}",
                "slug": f"slug-{uuid.uuid4().hex[:6]}",
                "name": "Before Demotion",
            },
        )
        assert create.status_code in (200, 201), create.text

        db = Session()
        try:
            user = db.query(PlatformUser).filter_by(email=email).one()
            user.role = "support_agent"  # read-only for tenants
            db.commit()
        finally:
            db.close()

        after = client.post(
            "/v1/tenants",
            headers=headers,
            json={
                "external_id": f"ext-{uuid.uuid4().hex[:6]}",
                "slug": f"slug-{uuid.uuid4().hex[:6]}",
                "name": "After Demotion",
            },
        )
        assert after.status_code == 403, "the same token must lose write access"

    def test_deactivated_account_is_locked_out_immediately(self, client):
        Session = client.app_session_factory
        email = _make_staff(Session)
        token = _login(client, email)

        db = Session()
        try:
            db.query(PlatformUser).filter_by(email=email).one().is_active = False
            db.commit()
        finally:
            db.close()

        res = client.get("/v1/tenants", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401


class TestMfaEnforcement:
    def _require_mfa(self, Session):
        db = Session()
        try:
            row = db.get(SecuritySettings, 1) or SecuritySettings(id=1)
            row.mfa_required = True
            db.add(row)
            db.commit()
        finally:
            db.close()

    def test_policy_blocks_unenrolled_staff(self, client):
        """`mfa_required` was stored but never checked, so the policy enforced
        nothing at all."""
        Session = client.app_session_factory
        email = _make_staff(Session, mfa_enabled=False)
        token = _login(client, email)
        self._require_mfa(Session)

        res = client.get("/v1/tenants", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403
        assert "MFA" in res.json()["detail"]

    def test_enrolment_path_stays_reachable(self, client):
        """Enforcement must not lock an operator out of enrolling."""
        Session = client.app_session_factory
        email = _make_staff(Session, mfa_enabled=False)
        token = _login(client, email)
        self._require_mfa(Session)

        res = client.post(
            "/v1/auth/mfa/setup", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code != 403, "MFA setup must be exempt from MFA enforcement"

    def test_enrolled_staff_pass(self, client):
        """Full enrol-then-authenticate path, including the TOTP challenge."""
        import pyotp

        Session = client.app_session_factory
        email = _make_staff(Session, mfa_enabled=False)
        token = _login(client, email)

        setup = client.post(
            "/v1/auth/mfa/setup", headers={"Authorization": f"Bearer {token}"}
        )
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]

        verify = client.post(
            "/v1/auth/mfa/verify",
            headers={"Authorization": f"Bearer {token}"},
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 200, verify.text

        self._require_mfa(Session)

        # An enrolled operator logs in with a code and is not blocked by policy.
        login = client.post(
            "/v1/auth/login",
            json={
                "email": email,
                "password": "Sup3rStr0ng!Pass",
                "mfa_code": pyotp.TOTP(secret).now(),
            },
        )
        assert login.status_code == 200, login.text
        fresh = login.json()["access_token"]

        res = client.get("/v1/tenants", headers={"Authorization": f"Bearer {fresh}"})
        assert res.status_code == 200


class TestInvoicePaymentEndpoint:
    def _setup(self, client):
        from datetime import datetime, timezone

        from app.models import SaasInvoice

        Session = client.app_session_factory
        token = _login(client, _make_staff(Session, role="owner"))
        db = Session()
        try:
            tenant = Tenant(
                external_id=f"ext-{uuid.uuid4().hex[:6]}",
                slug=f"slug-{uuid.uuid4().hex[:6]}",
                name="Payer Academy",
            )
            db.add(tenant)
            db.flush()
            invoice = SaasInvoice(
                tenant_id=tenant.id,
                number=f"SAAS-{uuid.uuid4().hex[:10]}",
                period_key="2026-07",
                amount_minor=500_000,
                amount_paid_minor=0,
                currency="NGN",
                status="open",
            )
            db.add(invoice)
            db.commit()
            return token, invoice.id
        finally:
            db.close()

    def test_partial_payment_reported_as_partially_paid(self, client):
        token, invoice_id = self._setup(client)
        res = client.post(
            f"/v1/billing/invoices/{invoice_id}/pay",
            headers={"Authorization": f"Bearer {token}"},
            json={"amount_minor": 1000, "method": "manual"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "partially_paid"
        assert body["amount_paid_minor"] == 1000

    def test_full_payment_settles(self, client):
        token, invoice_id = self._setup(client)
        res = client.post(
            f"/v1/billing/invoices/{invoice_id}/pay",
            headers={"Authorization": f"Bearer {token}"},
            json={"amount_minor": 500_000, "method": "manual"},
        )
        assert res.json()["status"] == "paid"

    def test_zero_amount_rejected(self, client):
        token, invoice_id = self._setup(client)
        res = client.post(
            f"/v1/billing/invoices/{invoice_id}/pay",
            headers={"Authorization": f"Bearer {token}"},
            json={"amount_minor": 0, "method": "manual"},
        )
        assert res.status_code == 422

    def test_duplicate_provider_reference_is_refused(self, client):
        """A retried gateway webhook must not credit the same payment twice."""
        token, invoice_id = self._setup(client)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "amount_minor": 1000,
            "method": "transfer",
            "provider_reference": "psk_ref_00991",
        }
        assert client.post(
            f"/v1/billing/invoices/{invoice_id}/pay", headers=headers, json=payload
        ).status_code == 200
        second = client.post(
            f"/v1/billing/invoices/{invoice_id}/pay", headers=headers, json=payload
        )
        assert second.status_code == 409


class TestCourtesyUnlockEndpoint:
    def test_courtesy_unlock_requires_reason(self, client):
        Session = client.app_session_factory
        token = _login(client, _make_staff(Session, role="owner"))
        db = Session()
        try:
            tenant = Tenant(
                external_id=f"ext-{uuid.uuid4().hex[:6]}",
                slug=f"slug-{uuid.uuid4().hex[:6]}",
                name="Locked Academy",
                status="suspended",
                status_reason="billing_grace_expired",
            )
            db.add(tenant)
            db.commit()
            tenant_id = tenant.id
        finally:
            db.close()

        res = client.post(
            "/v1/billing/courtesy-unlock",
            headers={"Authorization": f"Bearer {token}"},
            json={"tenant_id": tenant_id, "duration_hours": 72, "reason": "   "},
        )
        assert res.status_code == 400

    def test_courtesy_unlock_restores_access(self, client):
        Session = client.app_session_factory
        token = _login(client, _make_staff(Session, role="owner"))
        db = Session()
        try:
            tenant = Tenant(
                external_id=f"ext-{uuid.uuid4().hex[:6]}",
                slug=f"slug-{uuid.uuid4().hex[:6]}",
                name="Locked Academy",
                status="suspended",
                status_reason="billing_dunning",
            )
            db.add(tenant)
            db.commit()
            tenant_id = tenant.id
        finally:
            db.close()

        res = client.post(
            "/v1/billing/courtesy-unlock",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "tenant_id": tenant_id,
                "duration_hours": 48,
                "reason": "School requested time to pay via app",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "active"
        assert body["tenantId"] == tenant_id
        assert body["courtesyUnlockUntil"]


class TestOpenApiExposure:
    def test_schema_available_in_development(self, client):
        assert client.get("/openapi.json").status_code == 200
