"""Payroll, end to end over HTTP: profiles, a monthly run, two-person approval, the ledger,
the bank schedule and staff payslips."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.use_cases.payroll.notifications import SendPayslipReadyEmails
from app.use_cases.shared.ports import EmailMessage
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import _school, _today

pytestmark = pytest.mark.integration

PAY = "/api/v1/payroll"

TEACHER_PAY = {
    "basic_minor": 20_000_000,
    "allowances": [
        {"name": "Housing", "amount_minor": 8_000_000},
        {"name": "Transport", "amount_minor": 4_000_000},
    ],
    "deductions": [{"name": "Cooperative", "amount_minor": 1_000_000}],
    "annual_rent_minor": 120_000_000,
    "bank_name": "GTBank",
    "account_number": "0123 456 789",
    "tax_id": "TIN-1234",
}


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def _ledger(owner_dsn: str, tenant_id: str) -> dict[str, int]:
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            rows = await conn.fetch(
                "SELECT account, sum(amount_minor) AS total FROM finance_journal_lines "
                "WHERE tenant_id = $1::uuid GROUP BY account",
                tenant_id,
            )
    finally:
        await conn.close()
    return {r["account"]: int(r["total"]) for r in rows if int(r["total"]) != 0}


async def test_monthly_pay_run_from_profiles_to_payslips(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    owner_dsn: str,
    app_dsn: str,
) -> None:
    sc = await _school(client, container, mailbox, "payday")
    bursar, director, teacher = sc["bursar"], sc["director"], sc["teacher"]
    tenant_id = director["tenant_id"]

    # Nigerian schools start from the 2026 rules.
    settings = (await client.get(f"{PAY}/settings", headers=bearer(bursar))).json()
    assert settings["saved"] is False
    assert settings["pension_employee_bp"] == 800
    assert [b["rate_bp"] for b in settings["tax_bands"]] == [0, 1500, 1800, 2100, 2300, 2500]

    # Pay profiles: a payslip preview comes back straight away.
    made = await client.put(
        f"{PAY}/profiles/{teacher['user_id']}", headers=bearer(bursar), json=TEACHER_PAY
    )
    assert made.status_code == 200, made.text
    profile = made.json()
    assert profile["account_hint"] == "••••6789"
    assert "account_number" not in profile
    preview = profile["preview"]
    assert preview["gross_minor"] == 32_000_000
    assert preview["pension_employee_minor"] == 2_560_000
    assert preview["pension_employer_minor"] == 3_200_000
    assert preview["taxable_annual_minor"] == 329_280_000
    assert preview["paye_minor"] == 3_189_200
    assert preview["net_minor"] == 25_250_800

    own = await client.put(
        f"{PAY}/profiles/{bursar['user_id']}",
        headers=bearer(bursar),
        json={"basic_minor": 15_000_000, "bank_name": "Access", "account_number": "9876543210"},
    )
    assert own.status_code == 200, own.text
    assert own.json()["preview"]["net_minor"] == 12_730_000

    student = await client.put(
        f"{PAY}/profiles/{sc['s1']['user_id']}", headers=bearer(bursar), json={"basic_minor": 1}
    )
    assert student.status_code == 404
    too_much = await client.put(
        f"{PAY}/profiles/{teacher['user_id']}",
        headers=bearer(bursar),
        json={**TEACHER_PAY, "deductions": [{"name": "Loan", "amount_minor": 40_000_000}]},
    )
    assert too_much.status_code == 422
    assert too_much.json()["code"] == "negative_net_pay"
    bad_account = await client.put(
        f"{PAY}/profiles/{teacher['user_id']}",
        headers=bearer(bursar),
        json={**TEACHER_PAY, "account_number": "12ab"},
    )
    assert bad_account.status_code == 422

    # The account number is encrypted at rest.
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            stored = await conn.fetchval(
                "SELECT account_number_ciphertext FROM payroll_profiles WHERE staff_id = $1::uuid",
                teacher["user_id"],
            )
    finally:
        await conn.close()
    assert "0123456789" not in stored

    # Prepare the month as a draft; a second run for the same month is refused.
    today = _today()
    period = {"year": today.year, "month": today.month}
    prepared = await client.post(f"{PAY}/runs", headers=bearer(bursar), json=period)
    assert prepared.status_code == 201, prepared.text
    run = prepared.json()
    run_id = run["run"]["id"]
    assert run["run"]["status"] == "draft"
    assert run["run"]["totals"]["staff"] == 2
    assert run["run"]["totals"]["net_minor"] == 25_250_800 + 12_730_000
    assert [p["staff_name"] for p in run["payslips"]] == ["Bisi Bursar", "Tunde Teacher"]
    again = await client.post(f"{PAY}/runs", headers=bearer(bursar), json=period)
    assert again.status_code == 409
    assert again.json()["code"] == "run_exists"

    # A raise before approval: refresh picks it up.
    raised = await client.put(
        f"{PAY}/profiles/{bursar['user_id']}",
        headers=bearer(bursar),
        json={"basic_minor": 16_000_000, "bank_name": "Access"},
    )
    assert raised.json()["account_hint"] == "••••3210"  # the saved number is kept
    refreshed = (await client.post(f"{PAY}/runs/{run_id}/refresh", headers=bearer(bursar))).json()
    gross = 32_000_000 + 16_000_000
    assert refreshed["run"]["totals"]["gross_minor"] == gross

    # Two people: the preparer can't approve, the director can.
    self_approve = await client.post(f"{PAY}/runs/{run_id}/approve", headers=bearer(bursar))
    assert self_approve.status_code == 403
    early_schedule = await client.get(
        f"{PAY}/runs/{run_id}/bank-schedule.csv", headers=bearer(bursar)
    )
    assert early_schedule.status_code == 409
    approved = await client.post(f"{PAY}/runs/{run_id}/approve", headers=bearer(director))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    totals = approved.json()["totals"]
    assert (await client.post(f"{PAY}/runs/{run_id}/refresh", headers=bearer(bursar))).json()[
        "code"
    ] == "run_closed"

    # Approval accrues the cost; nothing has left the bank yet.
    ledger = await _ledger(owner_dsn, tenant_id)
    employer = totals["pension_employer_minor"]
    assert ledger["salary_expense"] == gross + employer
    assert ledger["salaries_payable"] == -totals["net_minor"]
    assert ledger["paye_payable"] == -totals["paye_minor"]
    assert ledger["pension_payable"] == -(totals["pension_employee_minor"] + employer)
    assert ledger["other_deductions_payable"] == -1_000_000
    assert "bank" not in ledger

    # Approved payslips are frozen, even for the database owner.
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("DELETE FROM payroll_payslips WHERE run_id = $1::uuid", run_id)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE payroll_runs SET approved_by = prepared_by WHERE id = $1::uuid",
                    run_id,
                )
    finally:
        await conn.close()
    conn = await asyncpg.connect(app_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE payroll_payslips SET net_minor = 1")
    finally:
        await conn.close()

    # The bank schedule has full account numbers and is logged.
    schedule = await client.get(f"{PAY}/runs/{run_id}/bank-schedule.csv", headers=bearer(bursar))
    assert schedule.status_code == 200
    assert schedule.headers["cache-control"] == "no-store"
    body = schedule.text.lstrip("﻿").splitlines()
    assert body[0] == "name,bank,account_number,amount,currency,narration"
    assert "Tunde Teacher,GTBank,0123456789,252508.00,NGN" in body[2]

    # Paying posts the bank movement and tells staff their payslip is ready.
    paid = await client.post(
        f"{PAY}/runs/{run_id}/paid", headers=bearer(bursar), json={"reference": "GTB-BATCH-77"}
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    ledger = await _ledger(owner_dsn, tenant_id)
    assert "salaries_payable" not in ledger
    assert ledger["bank"] == -totals["net_minor"]
    void_paid = await client.post(
        f"{PAY}/runs/{run_id}/void", headers=bearer(director), json={"reason": "Too late"}
    )
    assert void_paid.json()["code"] == "run_closed"

    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "payroll.run_paid"]
    assert len(events) == 1
    assert sorted(events[0]["staff_ids"]) == sorted([teacher["user_id"], bursar["user_id"]])
    sender = _Outbox()
    await SendPayslipReadyEmails(
        email=sender,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    assert {m.to for m in sender.sent} == {"teacher@payday.ng", "bursar@payday.ng"}
    assert all("252,508" not in m.text and "252508" not in m.text for m in sender.sent)

    # Staff see their own payslips, and nothing else of payroll.
    mine = (await client.get(f"{PAY}/my-payslips", headers=bearer(teacher))).json()["items"]
    assert len(mine) == 1
    assert mine[0]["status"] == "paid"
    assert mine[0]["payslip"]["net_minor"] == 25_250_800
    assert mine[0]["payslip"]["staff_name"] == "Tunde Teacher"
    for path in ("/runs", f"/runs/{run_id}", "/profiles", "/settings"):
        assert (await client.get(f"{PAY}{path}", headers=bearer(teacher))).status_code == 403

    # Every step is in the activity log.
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(director), params={"action": "payroll."}
    )
    actions = {e["action"] for e in log.json()["items"]}
    assert {
        "payroll.profile_created",
        "payroll.profile_updated",
        "payroll.run_prepared",
        "payroll.run_refreshed",
        "payroll.run_approved",
        "payroll.bank_schedule_exported",
        "payroll.run_paid",
    } <= actions


async def test_cancelling_runs_and_changing_settings(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, owner_dsn: str
) -> None:
    sc = await _school(client, container, mailbox, "payvoid")
    bursar, director, teacher = sc["bursar"], sc["director"], sc["teacher"]
    tenant_id = director["tenant_id"]

    empty = await client.post(
        f"{PAY}/runs", headers=bearer(bursar), json={"year": _today().year, "month": 1}
    )
    assert empty.status_code == 422
    assert empty.json()["code"] == "no_pay_profiles"
    await client.put(
        f"{PAY}/profiles/{teacher['user_id']}", headers=bearer(bursar), json=TEACHER_PAY
    )

    # The school isn't in the housing fund yet; it joins.
    current = (await client.get(f"{PAY}/settings", headers=bearer(bursar))).json()
    changed = await client.put(
        f"{PAY}/settings",
        headers=bearer(bursar),
        json={
            k: v
            for k, v in {**current, "housing_fund_bp": 250}.items()
            if k not in {"currency", "saved"}
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["saved"] is True
    bad = await client.put(
        f"{PAY}/settings",
        headers=bearer(bursar),
        json={
            **{k: v for k, v in current.items() if k not in {"currency", "saved"}},
            "tax_bands": [{"width_minor": None, "rate_bp": 1000}, {"width_minor": 5, "rate_bp": 0}],
        },
    )
    assert bad.status_code == 422

    today = _today()
    period = {"year": today.year, "month": today.month}
    run = (await client.post(f"{PAY}/runs", headers=bearer(bursar), json=period)).json()
    assert run["payslips"][0]["housing_fund_minor"] == 500_000  # 2.5% of basic
    far = await client.post(
        f"{PAY}/runs", headers=bearer(bursar), json={"year": today.year + 2, "month": 1}
    )
    assert far.status_code == 422

    # A draft is cancelled by the preparer; the month is free again.
    voided = await client.post(
        f"{PAY}/runs/{run['run']['id']}/void", headers=bearer(bursar), json={"reason": "Wrong"}
    )
    assert voided.json()["status"] == "void"
    second = (await client.post(f"{PAY}/runs", headers=bearer(bursar), json=period)).json()
    second_id = second["run"]["id"]
    assert (
        await client.post(f"{PAY}/runs/{second_id}/approve", headers=bearer(director))
    ).status_code == 200

    # An approved run needs an approver to cancel it, and the accrual is reversed.
    denied = await client.post(
        f"{PAY}/runs/{second_id}/void", headers=bearer(bursar), json={"reason": "Mistake"}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "approver_only"
    undone = await client.post(
        f"{PAY}/runs/{second_id}/void", headers=bearer(director), json={"reason": "Mistake"}
    )
    assert undone.status_code == 200, undone.text
    assert await _ledger(owner_dsn, tenant_id) == {}

    runs = (await client.get(f"{PAY}/runs", headers=bearer(director))).json()["items"]
    assert [r["status"] for r in runs] == ["void", "void"]
    assert runs[0]["totals"]["staff"] == 1
    mine = (await client.get(f"{PAY}/my-payslips", headers=bearer(teacher))).json()
    assert mine["items"] == []
