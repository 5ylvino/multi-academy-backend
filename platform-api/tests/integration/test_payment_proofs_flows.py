"""Parents, payment options and proof of payment over HTTP."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from masms.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import FIN, _fee, _member, _school, _statement, _today

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"receipt " * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300


async def _parent(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, sc: dict[str, Any], slug: str
) -> dict[str, Any]:
    parent = await _member(
        client,
        container,
        mailbox,
        sc["director"],
        slug=slug,
        email=f"parent@{slug}.ng",
        role="parent",
        name="Ngozi Obi",
    )
    r = await client.post(
        f"/api/v1/users/{sc['s1']['user_id']}/guardians",
        headers=bearer(sc["director"]),
        json={"parent_id": parent["user_id"], "relationship": "mother"},
    )
    assert r.status_code == 201, r.text
    return parent


def _form(student: dict[str, Any], **extra: str) -> dict[str, str]:
    return {
        "student_id": student["user_id"],
        "amount_minor": "6000000",
        "method": "bank_transfer",
        "paid_on": str(_today() - timedelta(days=1)),
        "reference": "FT2611-000123",
        **extra,
    }


async def test_parent_uploads_proof_and_bursar_approves(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "proofs")
    b, d = sc["bursar"], sc["director"]
    parent = await _parent(client, container, mailbox, sc, "proofs")
    await _fee(client, b, name="Tuition", amount_minor=10_000_000)
    assert (await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})).status_code == 200

    # The school's payment options: online off by default, receipts on, bank details shown.
    opts = await client.get(f"{FIN}/settings", headers=bearer(parent))
    assert opts.status_code == 200
    assert opts.json()["online_payments_enabled"] is False
    assert opts.json()["payment_proofs_enabled"] is True
    online = await client.put(
        f"{FIN}/settings",
        headers=bearer(b),
        json={"currency": "NGN", "default_due_days": 14, "online_payments_enabled": True},
    )
    assert online.status_code == 422
    assert online.json()["code"] == "gateway_not_configured"
    banks = await client.put(
        f"{FIN}/settings",
        headers=bearer(b),
        json={
            "currency": "NGN",
            "default_due_days": 14,
            "payment_instructions": "Use the student's name as the narration.",
            "bank_accounts": [
                {
                    "bank_name": "GTBank",
                    "account_name": "Proofs Academy",
                    "account_number": "0123456789",
                }
            ],
        },
    )
    assert banks.status_code == 200, banks.text
    assert (await client.get(f"{FIN}/settings", headers=bearer(parent))).json()["bank_accounts"][0][
        "account_number"
    ] == "0123456789"

    # The parent sees the linked child's fees, and nobody else's.
    kids = await client.get("/api/v1/me/children", headers=bearer(parent))
    assert [k["student_id"] for k in kids.json()] == [sc["s1"]["user_id"]]
    assert (await _statement(client, parent, sc["s1"]))["owed"]["amount_minor"] == 10_000_000
    other = await client.get(
        f"{FIN}/students/{sc['s2']['user_id']}/statement", headers=bearer(parent)
    )
    assert other.status_code == 403
    invoices = await client.get(
        f"{FIN}/invoices", headers=bearer(parent), params={"student_id": sc["s1"]["user_id"]}
    )
    assert len(invoices.json()["items"]) == 1

    # Uploading: wrong type, someone else's child, then a real receipt.
    fake = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"]),
        files={
            "file": ("receipt.pdf", b"<script>alert(1)</script>" + b" " * 200, "application/pdf")
        },
    )
    assert fake.status_code == 422
    assert fake.json()["code"] == "unsupported_file"
    not_mine = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s2"]),
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
    )
    assert not_mine.status_code == 403
    sent = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], payer_name="Ngozi Obi"),
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
    )
    assert sent.status_code == 201, sent.text
    proof = sent.json()
    assert proof["status"] == "submitted"
    assert proof["file_type"] == "application/pdf"

    again = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], reference="ft 2611 000123"),
        files={"file": ("other.png", PNG, "image/png")},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_reference"
    same_file = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], reference="FT-OTHER-999"),
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
    )
    assert same_file.status_code == 409
    assert same_file.json()["code"] == "duplicate_file"

    # Nothing is credited until the bursar checks the bank statement.
    assert (await _statement(client, b, sc["s1"]))["paid"]["amount_minor"] == 0
    queue = await client.get(
        f"{FIN}/payment-proofs", headers=bearer(b), params={"status": "submitted"}
    )
    assert [p["id"] for p in queue.json()["items"]] == [proof["id"]]
    download = await client.get(f"{FIN}/payment-proofs/{proof['id']}/file", headers=bearer(b))
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    teacher_peek = await client.get(
        f"{FIN}/payment-proofs/{proof['id']}/file", headers=bearer(sc["teacher"])
    )
    assert teacher_peek.status_code == 404

    parent_approve = await client.post(
        f"{FIN}/payment-proofs/{proof['id']}/approve", headers=bearer(parent), json={}
    )
    assert parent_approve.status_code == 403
    no_note = await client.post(
        f"{FIN}/payment-proofs/{proof['id']}/approve",
        headers=bearer(b),
        json={"amount_minor": 5_000_000},
    )
    assert no_note.status_code == 422
    approved = await client.post(
        f"{FIN}/payment-proofs/{proof['id']}/approve",
        headers=bearer(b),
        json={"amount_minor": 5_000_000, "note": "Statement shows N50,000"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    payment_id = approved.json()["payment_id"]
    payment = await client.get(f"{FIN}/payments/{payment_id}", headers=bearer(parent))
    assert payment.status_code == 200
    assert payment.json()["method"] == "bank_transfer"
    assert payment.json()["reference"] == "FT2611-000123"
    stmt = await _statement(client, parent, sc["s1"])
    assert stmt["owed"]["amount_minor"] == 5_000_000

    twice = await client.post(
        f"{FIN}/payment-proofs/{proof['id']}/approve", headers=bearer(d), json={}
    )
    assert twice.status_code == 409

    # A manually recorded payment with the same reference can't be claimed again by upload.
    later = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], reference="FT2611000123"),
        files={"file": ("new.png", PNG, "image/png")},
    )
    assert later.status_code == 409

    # Reject and withdraw.
    wrong = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], reference="FT-BAD-0001"),
        files={"file": ("new.png", PNG, "image/png")},
    )
    assert wrong.status_code == 201, wrong.text
    rejected = await client.post(
        f"{FIN}/payment-proofs/{wrong.json()['id']}/reject",
        headers=bearer(b),
        json={"reason": "No such transfer on our statement"},
    )
    assert rejected.json()["status"] == "rejected"
    mine = await client.get(f"{FIN}/payment-proofs", headers=bearer(parent))
    assert {p["status"] for p in mine.json()["items"]} == {"approved", "rejected"}

    pending = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"], reference="FT-WD-0002"),
        files={"file": ("w.png", PNG + b"1", "image/png")},
    )
    withdrawn = await client.post(
        f"{FIN}/payment-proofs/{pending.json()['id']}/withdraw", headers=bearer(parent)
    )
    assert withdrawn.json()["status"] == "withdrawn"


async def test_schools_can_turn_receipt_uploads_off_and_limits_hold(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "noproof")
    parent = await _parent(client, container, mailbox, sc, "noproof")
    off = await client.put(
        f"{FIN}/settings",
        headers=bearer(sc["bursar"]),
        json={"currency": "NGN", "default_due_days": 14, "payment_proofs_enabled": False},
    )
    assert off.status_code == 200
    assert off.json()["payment_proofs_enabled"] is False
    refused = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"]),
        files={"file": ("receipt.pdf", PDF, "application/pdf")},
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "payment_proofs_disabled"

    huge = await client.post(
        f"{FIN}/payment-proofs",
        headers=bearer(parent),
        data=_form(sc["s1"]),
        files={"file": ("big.pdf", b"%PDF-" + b"0" * (7 * 1024 * 1024), "application/pdf")},
    )
    assert huge.status_code == 413

    # Unlinking removes the parent's access straight away.
    unlink = await client.delete(
        f"/api/v1/users/{sc['s1']['user_id']}/guardians/{parent['user_id']}",
        headers=bearer(sc["director"]),
    )
    assert unlink.status_code == 204
    gone = await client.get(
        f"{FIN}/students/{sc['s1']['user_id']}/statement", headers=bearer(parent)
    )
    assert gone.status_code == 403
    teacher_link = await client.post(
        f"/api/v1/users/{sc['s1']['user_id']}/guardians",
        headers=bearer(sc["director"]),
        json={"parent_id": sc["teacher"]["user_id"]},
    )
    assert teacher_link.status_code == 422
