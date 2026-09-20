"""Platform RBAC: fixed roles → permission sets (CONTROL-SYSTEM-BUILD.md §4)."""

ALL_PERMISSIONS = [
    "dashboard:read",
    "tenants:read",
    "tenants:write",
    "tenants:blacklist",
    "plans:read",
    "plans:write",
    "subscriptions:read",
    "subscriptions:write",
    "deals:read",
    "deals:write",
    "billing:read",
    "billing:write",
    "usage:read",
    "flags:read",
    "flags:write",
    "providers:read",
    "providers:write",
    "secrets:write",
    "blocks:read",
    "blocks:write",
    "abuse:read",
    "abuse:write",
    "support:break_glass",
    "tickets:read",
    "tickets:write",
    "maintenance:write",
    "audit:read",
    "platform_users:read",
    "platform_users:write",
    "service_clients:write",
    "compliance:read",
    "compliance:write",
    "incidents:read",
    "incidents:write",
    "dual_control:approve",
    "monitoring:read",
]

_READ_ONLY = [p for p in ALL_PERMISSIONS if p.endswith(":read")]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "owner": ALL_PERMISSIONS,
    "ops_admin": _READ_ONLY + [
        "tenants:write",
        "flags:write",
        "providers:write",
        "secrets:write",
        "maintenance:write",
        "abuse:write",
        "support:break_glass",
        "incidents:write",
        "compliance:write",
        "dual_control:approve",
    ],
    "billing_admin": _READ_ONLY
    + ["plans:write", "subscriptions:write", "deals:write", "billing:write"],
    "support_agent": _READ_ONLY + ["support:break_glass", "tickets:write", "compliance:write"],
    "security_analyst": _READ_ONLY
    + ["blocks:write", "tenants:blacklist", "abuse:write", "dual_control:approve", "compliance:write"],
    "auditor": _READ_ONLY,
    "engineer_staging": _READ_ONLY + ["flags:write", "providers:write", "incidents:write"],
}


def permissions_for_role(role: str) -> list[str]:
    return ROLE_PERMISSIONS.get(role, [])
