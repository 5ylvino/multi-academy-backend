"""Inviting staff and students, accepting invites, and listing school accounts."""

from __future__ import annotations

import httpx
import pytest

from masms.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, register_and_verify

pytestmark = pytest.mark.integration

STAFF_PASSWORD = "Teacher-Passphrase-2026"


async def invite(
    client: httpx.AsyncClient,
    session: dict[str, str],
    *,
    email: str,
    roles: list[str],
    capabilities: list[str] | None = None,
) -> httpx.Response:
    return await client.post(
        "/api/v1/users",
        headers=bearer(session),
        json={
            "email": email,
            "full_name": "Chidi Okafor",
            "roles": roles,
            "capabilities": capabilities or [],
        },
    )


async def test_invite_accept_and_sign_in(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await register_and_verify(
        client, container, mailbox, slug="unity", email="head@unity.ng"
    )
    resp = await invite(client, director, email="chidi@unity.ng", roles=["subject_teacher"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending"

    # A pending account can't sign in before the invite is accepted.
    early = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": "unity", "email": "chidi@unity.ng", "password": STAFF_PASSWORD},
    )
    assert early.status_code == 401

    await container.outbox.run_once()
    token = mailbox.last_invite_token("chidi@unity.ng")

    weak = await client.post(
        "/api/v1/schools/unity/invites/accept", json={"token": token, "password": "password12"}
    )
    assert weak.status_code == 422

    accepted = await client.post(
        "/api/v1/schools/unity/invites/accept", json={"token": token, "password": STAFF_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["roles"] == ["subject_teacher"]

    again = await client.post(
        "/api/v1/schools/unity/invites/accept", json={"token": token, "password": STAFF_PASSWORD}
    )
    assert again.status_code in (404, 409)

    signed_in = await client.post(
        "/api/v1/auth/sign-in",
        json={"school": "unity", "email": "chidi@unity.ng", "password": STAFF_PASSWORD},
    )
    assert signed_in.status_code == 200, signed_in.text


async def test_invite_token_is_school_bound(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    a = await register_and_verify(client, container, mailbox, slug="alpha", email="h@alpha.ng")
    await register_and_verify(client, container, mailbox, slug="beta", email="h@beta.ng")
    assert (await invite(client, a, email="t@alpha.ng", roles=["class_teacher"])).status_code == 201
    await container.outbox.run_once()
    token = mailbox.last_invite_token("t@alpha.ng")
    wrong_school = await client.post(
        "/api/v1/schools/beta/invites/accept", json={"token": token, "password": STAFF_PASSWORD}
    )
    assert wrong_school.status_code == 404


async def test_duplicate_and_privileged_invites(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await register_and_verify(
        client, container, mailbox, slug="crest", email="head@crest.ng"
    )
    assert (await invite(client, director, email="x@crest.ng", roles=["bursar"])).status_code == 201
    dup = await invite(client, director, email="X@crest.ng", roles=["bursar"])
    assert dup.status_code == 409

    bad_role = await invite(client, director, email="y@crest.ng", roles=["wizard"])
    assert bad_role.status_code == 422

    # A teacher can't invite anyone.
    await invite(client, director, email="t@crest.ng", roles=["subject_teacher"])
    await container.outbox.run_once()
    teacher = await client.post(
        "/api/v1/schools/crest/invites/accept",
        json={"token": mailbox.last_invite_token("t@crest.ng"), "password": STAFF_PASSWORD},
    )
    forbidden = await invite(client, teacher.json(), email="z@crest.ng", roles=["student"])
    assert forbidden.status_code == 403


async def test_list_users_filters_and_paginates(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await register_and_verify(
        client, container, mailbox, slug="maple", email="head@maple.ng"
    )
    for i in range(5):
        r = await invite(client, director, email=f"s{i}@maple.ng", roles=["student"])
        assert r.status_code == 201, r.text
    await invite(client, director, email="teach@maple.ng", roles=["class_teacher"])

    students = await client.get(
        "/api/v1/users", params={"role": "student", "limit": 2}, headers=bearer(director)
    )
    assert students.status_code == 200, students.text
    first = students.json()
    assert len(first["items"]) == 2
    assert first["page"]["next_cursor"]

    seen = {u["email"] for u in first["items"]}
    cursor = first["page"]["next_cursor"]
    while cursor:
        page = await client.get(
            "/api/v1/users",
            params={"role": "student", "limit": 2, "cursor": cursor},
            headers=bearer(director),
        )
        body = page.json()
        seen |= {u["email"] for u in body["items"]}
        cursor = body["page"]["next_cursor"]
    assert seen == {f"s{i}@maple.ng" for i in range(5)}

    search = await client.get("/api/v1/users", params={"q": "teach@"}, headers=bearer(director))
    assert [u["email"] for u in search.json()["items"]] == ["teach@maple.ng"]

    bad_cursor = await client.get(
        "/api/v1/users", params={"cursor": "!!!"}, headers=bearer(director)
    )
    assert bad_cursor.status_code == 422
