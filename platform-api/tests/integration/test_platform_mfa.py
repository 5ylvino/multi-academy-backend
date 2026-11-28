"""Owner-console two-step verification over HTTP."""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.helpers import (
    bearer,
    code_for,
    create_platform_user,
    enrol_mfa,
    platform_session,
    platform_sign_in,
)

pytestmark = pytest.mark.integration
API = "/api/v1/platform/auth"


async def _verify(
    client: httpx.AsyncClient, challenge: dict[str, Any], code: str
) -> httpx.Response:
    return await client.post(
        f"{API}/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": code}
    )


async def test_password_alone_never_yields_a_console_session(
    client: httpx.AsyncClient, container: Container
) -> None:
    email = await create_platform_user(container, ["owner"])
    first = await platform_sign_in(client, email)
    assert first["next_step"] == "enrol"
    assert "access_token" not in first

    # The enrol challenge can't be used to skip straight to verification.
    assert (await _verify(client, first, "000000")).status_code == 401

    enrolled = await enrol_mfa(client, first)
    assert enrolled["setup"]["otpauth_uri"].startswith("otpauth://totp/")
    assert len(enrolled["recovery_codes"]) == 10
    assert len(set(enrolled["recovery_codes"])) == 10
    me = await client.get(f"{API}/me", headers=bearer(enrolled["session"]))
    assert me.status_code == 200

    second = await platform_sign_in(client, email)
    assert second["next_step"] == "verify"
    ok = await _verify(client, second, code_for(enrolled["secret"], offset=1))
    assert ok.status_code == 200, ok.text
    assert ok.headers["cache-control"] == "no-store"

    # A challenge works once.
    assert (
        await _verify(client, second, code_for(enrolled["secret"], offset=1))
    ).status_code == 401


async def test_codes_cannot_be_replayed(client: httpx.AsyncClient, container: Container) -> None:
    email = await create_platform_user(container, ["owner"])
    enrolled = await enrol_mfa(client, await platform_sign_in(client, email))
    # Enrolment used the current step; that same code must not sign in again.
    again = await _verify(
        client, await platform_sign_in(client, email), code_for(enrolled["secret"])
    )
    assert again.status_code == 401
    assert again.json()["code"] == "mfa_code_invalid"


async def test_recovery_codes_are_single_use(
    client: httpx.AsyncClient, container: Container, owner_dsn: str
) -> None:
    email = await create_platform_user(container, ["owner"])
    enrolled = await enrol_mfa(client, await platform_sign_in(client, email))
    code = enrolled["recovery_codes"][0]
    used = await _verify(client, await platform_sign_in(client, email), code.upper())
    assert used.status_code == 200, used.text
    reused = await _verify(client, await platform_sign_in(client, email), code)
    assert reused.status_code == 401

    conn = await asyncpg.connect(owner_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT secret_ciphertext, cardinality(recovery_code_hashes) AS left "
            "FROM platform_mfa_factors"
        )
        await conn.execute("SET app.rls_bypass = 'on'")  # the audit trail has RLS
        audited = await conn.fetchval(
            "SELECT count(*) FROM audit_events WHERE action = 'identity.mfa_recovery_code_used'"
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["left"] == 9
    assert enrolled["setup"]["secret"] not in row["secret_ciphertext"]
    assert audited == 1


async def test_wrong_codes_lock_the_account(
    client: httpx.AsyncClient, container: Container
) -> None:
    email = await create_platform_user(container, ["owner"])
    await enrol_mfa(client, await platform_sign_in(client, email))
    statuses = []
    for _ in range(5):
        challenge = await platform_sign_in(client, email)
        statuses.append((await _verify(client, challenge, "000000")).status_code)
    assert statuses == [401] * 5
    locked = await client.post(
        f"{API}/sign-in", json={"email": email, "password": "Strong-Passphrase-2026"}
    )
    assert locked.status_code == 423


async def test_challenge_closes_after_too_many_attempts(
    client: httpx.AsyncClient, container: Container
) -> None:
    email = await create_platform_user(container, ["owner"])
    enrolled = await enrol_mfa(client, await platform_sign_in(client, email))
    challenge = await platform_sign_in(client, email)
    for _ in range(4):
        assert (await _verify(client, challenge, "000000")).status_code == 401
    # The fifth wrong code locks the account and closes the challenge.
    assert (await _verify(client, challenge, "000000")).status_code == 401
    late = await _verify(client, challenge, code_for(enrolled["secret"], offset=1))
    assert late.status_code in (401, 423)


async def test_regenerate_recovery_codes_needs_a_live_code(
    client: httpx.AsyncClient, container: Container
) -> None:
    email = await create_platform_user(container, ["owner"])
    enrolled = await enrol_mfa(client, await platform_sign_in(client, email))
    h = bearer(enrolled["session"])
    bad = await client.post(f"{API}/mfa/recovery-codes", headers=h, json={"code": "000000"})
    assert bad.status_code == 401
    good = await client.post(
        f"{API}/mfa/recovery-codes",
        headers=h,
        json={"code": code_for(enrolled["secret"], offset=1)},
    )
    assert good.status_code == 200, good.text
    fresh = good.json()["recovery_codes"]
    assert len(fresh) == 10
    assert not set(fresh) & set(enrolled["recovery_codes"])
    old = await _verify(
        client, await platform_sign_in(client, email), enrolled["recovery_codes"][0]
    )
    assert old.status_code == 401


async def test_admin_reset_signs_out_and_forces_reenrolment(
    client: httpx.AsyncClient, container: Container
) -> None:
    owner = await platform_session(client, container, ["owner"])
    staff_email = await create_platform_user(container, ["support_agent"])
    staff = await enrol_mfa(client, await platform_sign_in(client, staff_email))
    staff_id = staff["session"]["user_id"]

    self_reset = await client.post(
        f"/api/v1/platform/users/{owner['user_id']}/mfa/reset", headers=bearer(owner)
    )
    assert self_reset.status_code == 403
    by_staff = await client.post(
        f"/api/v1/platform/users/{owner['user_id']}/mfa/reset", headers=bearer(staff["session"])
    )
    assert by_staff.status_code == 403

    reset = await client.post(f"/api/v1/platform/users/{staff_id}/mfa/reset", headers=bearer(owner))
    assert reset.status_code == 204
    refreshed = await client.post(
        f"{API}/refresh", json={"refresh_token": staff["session"]["refresh_token"]}
    )
    assert refreshed.status_code == 401
    assert (await platform_sign_in(client, staff_email))["next_step"] == "enrol"


async def test_invalid_challenge_token(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"{API}/mfa/verify", json={"challenge_token": "x" * 43, "code": "123456"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "mfa_challenge_invalid"
