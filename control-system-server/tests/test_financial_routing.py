import pytest
from pydantic import ValidationError

from app.models import FeeSplitRule, PayrollAccount
from app.models import PayrollRun, Tenant
from app.routers.financial import _get_payroll_run_for_tenant
from app.schemas import FeeSplitRuleSet
from app.services.runtime_config import build_runtime_config


def test_fee_split_contract_requires_exact_total():
    with pytest.raises(ValidationError, match="total 10000"):
        FeeSplitRuleSet(
            tenant_id=1,
            allocations=[
                {"destination": "school", "percentage_bps": 9000},
            ],
        )


def test_fee_split_contract_excludes_kira():
    with pytest.raises(ValidationError, match="KIRA"):
        FeeSplitRuleSet(
            tenant_id=1,
            provider_id="kira",
            allocations=[
                {"destination": "school", "percentage_bps": 10000},
            ],
        )


def test_runtime_config_exposes_fee_split_and_separated_payroll_accounts(session, tenant):
    session.add(
        FeeSplitRule(
            tenant_id=tenant.id,
            fee_type="school_fees",
            provider_id="paystack",
            allocations=[
                {"destination": "school", "percentage_bps": 9500},
                {"destination": "platform", "percentage_bps": 500},
            ],
        )
    )
    session.add_all(
        [
            PayrollAccount(
                tenant_id=tenant.id,
                account_role="salary",
                provider_id="bank_adapter",
                account_reference="salary-acct",
            ),
            PayrollAccount(
                tenant_id=tenant.id,
                account_role="operating",
                provider_id="bank_adapter",
                account_reference="operating-acct",
            ),
        ]
    )
    session.commit()

    payload = build_runtime_config(session, tenant)

    assert payload["feeSplits"][0]["providerId"] == "paystack"
    assert payload["feeSplits"][0]["allocations"][0]["percentage_bps"] == 9500
    assert payload["payroll"]["salaryAccountSeparated"] is True


def test_payroll_run_lookup_is_tenant_scoped(session, tenant):
    other = Tenant(
        external_id="other-ext",
        slug="other-school",
        name="Other Academy",
        status="active",
    )
    session.add(other)
    session.flush()
    account = PayrollAccount(
        tenant_id=other.id,
        account_role="salary",
        provider_id="bank_adapter",
        account_reference="other-salary",
    )
    session.add(account)
    session.flush()
    run = PayrollRun(
        tenant_id=other.id,
        period_key="2026-08",
        provider_id="bank_adapter",
        salary_account_id=account.id,
        gross_minor=100,
        currency="NGN",
        status="pending_approval",
        created_by="other@example.com",
    )
    session.add(run)
    session.commit()

    assert _get_payroll_run_for_tenant(session, tenant.id, run.id) is None
    assert _get_payroll_run_for_tenant(session, other.id, run.id).id == run.id
