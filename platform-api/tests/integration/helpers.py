from __future__ import annotations

from typing import Any

import httpx

from masms.infrastructure.container import Container
from masms.use_cases.identity.create_platform_user import CreatePlatformUserCommand
from tests.integration.conftest import Mailbox

ADMIN_PASSWORD = "Strong-Passphrase-2026"


async def register_and_verify(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, *, slug: str, email: str
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/schools",
        json={
            "school_name": f"{slug.title()} Academy",
            "slug": slug,
            "school_levels": ["primary", "secondary"],
            "admin_full_name": "Ngozi Eze",
            "admin_email": email,
            "admin_password": ADMIN_PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending_verification"
    await container.outbox.run_once()
    verify = await client.post(
        f"/api/v1/schools/{slug}/verify", json={"email": email, "code": mailbox.last_code()}
    )
    assert verify.status_code == 200, verify.text
    body: dict[str, Any] = verify.json()
    return body


async def platform_session(
    client: httpx.AsyncClient, container: Container, roles: list[str]
) -> dict[str, Any]:
    email = f"{'-'.join(roles)}@mas.ng"
    await container.use_cases.create_platform_user(
        CreatePlatformUserCommand(
            email=email, full_name="Platform Staff", password=ADMIN_PASSWORD, roles=roles
        ),
        actor=None,
    )
    resp = await client.post(
        "/api/v1/platform/auth/sign-in", json={"email": email, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def bearer(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}
