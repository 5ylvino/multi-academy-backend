from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.identity import totp
from app.infrastructure.container import Container
from app.use_cases.identity.create_platform_user import CreatePlatformUserCommand
from tests.integration.conftest import Mailbox

ADMIN_PASSWORD = "Strong-Passphrase-2026"


async def register_and_verify(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    *,
    slug: str,
    email: str,
    **extra: Any,
) -> dict[str, Any]:
    """Registers and verifies a school (primary and secondary unless ``extra`` says otherwise)."""
    resp = await client.post(
        "/api/v1/schools",
        json={
            "school_name": f"{slug.title()} Academy",
            "slug": slug,
            "school_levels": ["primary", "secondary"],
            "admin_full_name": "Ngozi Eze",
            "admin_email": email,
            "admin_password": ADMIN_PASSWORD,
            **extra,
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


def secret_from_setup(setup: dict[str, Any]) -> bytes:
    raw = str(setup["secret"])
    return base64.b32decode(raw + "=" * (-len(raw) % 8))


def code_for(secret: bytes, *, offset: int = 0) -> str:
    """The app code for the current 30 s step plus ``offset`` steps (the server allows +-1)."""
    return totp.code_at(secret, totp.time_step(datetime.now(UTC)) + offset)


async def platform_sign_in(client: httpx.AsyncClient, email: str) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/platform/auth/sign-in", json={"email": email, "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def enrol_mfa(client: httpx.AsyncClient, challenge: dict[str, Any]) -> dict[str, Any]:
    """Run enrolment for an ``enrol`` challenge. Returns session, recovery codes and secret."""
    assert challenge["next_step"] == "enrol", challenge
    setup = await client.post(
        "/api/v1/platform/auth/mfa/enrolment",
        json={"challenge_token": challenge["challenge_token"]},
    )
    assert setup.status_code == 200, setup.text
    secret = secret_from_setup(setup.json())
    done = await client.post(
        "/api/v1/platform/auth/mfa/enrolment/confirm",
        json={"challenge_token": challenge["challenge_token"], "code": code_for(secret)},
    )
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    body["secret"] = secret
    body["setup"] = setup.json()
    return body


async def create_platform_user(container: Container, roles: list[str]) -> str:
    email = f"{'-'.join(roles)}@mas.ng"
    await container.use_cases.create_platform_user(
        CreatePlatformUserCommand(
            email=email, full_name="Platform Staff", password=ADMIN_PASSWORD, roles=roles
        ),
        actor=None,
    )
    return email


async def platform_session(
    client: httpx.AsyncClient, container: Container, roles: list[str]
) -> dict[str, Any]:
    """A signed-in console session, created through the real password + MFA enrolment flow."""
    email = await create_platform_user(container, roles)
    enrolled = await enrol_mfa(client, await platform_sign_in(client, email))
    session: dict[str, Any] = enrolled["session"]
    return session


def bearer(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


SECTION_ROLES = frozenset(
    {
        "head_teacher",
        "assistant_head_teacher",
        "principal",
        "vice_principal",
        "class_teacher",
        "subject_teacher",
    }
)


async def section_id(client: httpx.AsyncClient, who: dict[str, Any], level: str) -> str:
    """The id of the section holding ``level``."""
    r = await client.get("/api/v1/academics/sections", headers=bearer(who))
    assert r.status_code == 200, r.text
    for section in r.json():
        if level in section["levels"]:
            return str(section["id"])
    raise AssertionError(f"no section holds {level}")


async def invite_body(
    client: httpx.AsyncClient,
    director: dict[str, Any],
    *,
    email: str,
    name: str,
    role: str,
    level: str | None = None,
) -> dict[str, Any]:
    """An invite request; heads and teachers are placed in the fitting section."""
    body: dict[str, Any] = {"email": email, "full_name": name, "roles": [role]}
    if role in SECTION_ROLES:
        want = "secondary" if role in {"principal", "vice_principal"} else (level or "primary")
        body["section_ids"] = [await section_id(client, director, want)]
    return body
