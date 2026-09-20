from app.models import TenantDomain
from app.services.runtime_config import build_runtime_config, invalidate_runtime_config_cache


def test_runtime_config_exposes_only_active_tenant_domains(session, tenant):
    session.add_all(
        [
            TenantDomain(
                tenant_id=tenant.id,
                hostname="school.example.com",
                kind="subdomain",
                status="active",
                ssl_status="active",
                is_primary=True,
            ),
            TenantDomain(
                tenant_id=tenant.id,
                hostname="pending.school.example.com",
                kind="custom",
                status="pending",
                ssl_status="pending",
            ),
        ]
    )
    session.commit()
    invalidate_runtime_config_cache(tenant.id)

    payload = build_runtime_config(session, tenant)

    assert payload["domains"] == [
        {
            "hostname": "school.example.com",
            "kind": "subdomain",
            "isPrimary": True,
            "sslStatus": "active",
        }
    ]
