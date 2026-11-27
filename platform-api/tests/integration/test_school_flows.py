"""End-to-end school flows over HTTP against real PostgreSQL and Redis."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import ADMIN_PASSWORD, bearer, register_and_verify

pytestmark = pytest.mark.integration


async def test_register_verify_bootstrap_and_me(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    session = await register_and_verify(
        client, container, mailbox, slug="greenfield", email="head@greenfield.ng"
    )
    assert session["realm"] == "school"
    assert session["roles"] == ["director"]

    boot = await client.get("/api/v1/bootstrap", headers=bearer(session))
    assert boot.status_code == 200, boot.text
    data = boot.json()
    assert data["slug"] == "greenfield"
    assert data["status"] == "trial"
    assert data["school_levels"] == ["primary", "secondary"]
    assert "*" in data["permissions"]

    me = await client.get("/api/v1/auth/me", headers=bearer(session))
    assert me.status_code == 200
    assert me.json()["email"] == "head@greenfield.ng"


async def test_verification_code_is_redacted_after_delivery(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, owner_dsn: str
) -> None:
    await register_and_verify(client, container, mailbox, slug="redact", email="a@redact.ng")
    conn = await asyncpg.connect(owner_dsn)
    try:
        payload = await conn.fetchval(
            "SELECT payload->>'code' FROM outbox_events "
            "WHERE name = 'tenancy.verification_code_issued'"
        )
    finally:
        await conn.close()
    assert payload == "[redacted]"


async def test_wrong_code_is_counted_and_rejected(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    resp = await client.post(
        "/api/v1/schools",
        json={
            "school_name": "Bluebell School",
            "school_levels": ["nursery"],
            "admin_full_name": "Tunde Bello",
            "admin_email": "tunde@bluebell.ng",
            "admin_password": ADMIN_PASSWORD,
        },
    )
    assert resp.status_code == 201
    slug = resp.json()["slug"]
    assert slug == "bluebell-school"
    bad = await client.post(
        f"/api/v1/schools/{slug}/verify", json={"email": "tunde@bluebell.ng", "code": "000000"}
    )
    assert bad.status_code == 422
    assert bad.json()["code"] in {"verification_wrong"}
    # Pending school cannot sign in yet.
    signin = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": slug, "email": "tunde@bluebell.ng", "password": ADMIN_PASSWORD},
    )
    assert signin.status_code == 403
    assert signin.json()["code"] == "tenant_unavailable"


async def test_duplicate_slug_conflicts(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    await register_and_verify(client, container, mailbox, slug="dup", email="a@dup.ng")
    resp = await client.post(
        "/api/v1/schools",
        json={
            "school_name": "Dup",
            "slug": "dup",
            "school_levels": ["primary"],
            "admin_full_name": "X Y",
            "admin_email": "b@dup.ng",
            "admin_password": ADMIN_PASSWORD,
        },
    )
    assert resp.status_code == 409


async def test_sign_in_refresh_rotation_and_reuse_detection(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    await register_and_verify(client, container, mailbox, slug="rotate", email="head@rotate.ng")
    signin = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": "rotate", "email": "HEAD@rotate.ng", "password": ADMIN_PASSWORD},
    )
    assert signin.status_code == 200, signin.text
    first = signin.json()["refresh_token"]

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert refreshed.status_code == 200
    second = refreshed.json()["refresh_token"]
    assert second != first

    # Replaying the rotated token revokes the whole family, including the newest token.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert after.status_code == 401


async def test_wrong_password_and_lockout(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    await register_and_verify(client, container, mailbox, slug="lockout", email="head@lockout.ng")
    for _ in range(5):
        bad = await client.post(
            "/api/v1/auth/sign-in",
            json={"school": "lockout", "email": "head@lockout.ng", "password": "wrong-password-1"},
        )
        assert bad.status_code == 401
        assert bad.json()["title"] == "Email or password is incorrect."
    locked = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": "lockout", "email": "head@lockout.ng", "password": ADMIN_PASSWORD},
    )
    assert locked.status_code == 423


async def test_unknown_school_and_unknown_email_look_the_same(client: httpx.AsyncClient) -> None:
    a = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": "nowhere", "email": "x@y.ng", "password": "whatever-123"},
    )
    assert a.status_code == 401
    assert a.json()["code"] == "authentication_failed"


async def test_sign_out_revokes_refresh(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    session = await register_and_verify(
        client, container, mailbox, slug="signout", email="head@signout.ng"
    )
    out = await client.post(
        "/api/v1/auth/sign-out", json={"refresh_token": session["refresh_token"]}
    )
    assert out.status_code == 204
    again = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
    )
    assert again.status_code == 401


async def test_school_token_rejected_on_platform_routes(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    session = await register_and_verify(
        client, container, mailbox, slug="realm", email="head@realm.ng"
    )
    resp = await client.get("/api/v1/platform/tenants", headers=bearer(session))
    assert resp.status_code == 401


async def test_missing_token_and_validation_problem_shape(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/bootstrap")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers["x-request-id"]
    bad = await client.post("/api/v1/schools", json={"school_name": "x"})
    assert bad.status_code == 422
    assert bad.json()["code"] == "validation_failed"
