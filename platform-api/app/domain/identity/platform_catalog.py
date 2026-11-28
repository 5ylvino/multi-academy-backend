"""Platform (owner console) role catalog, ported from the control server's RBAC."""

from __future__ import annotations

from types import MappingProxyType

from app.domain.identity.roles import PlatformRole

ALL_PLATFORM_PERMISSIONS: frozenset[str] = frozenset(
    {
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
    }
)

_READ_ONLY = frozenset(p for p in ALL_PLATFORM_PERMISSIONS if p.endswith(":read"))

PLATFORM_ROLE_PERMISSIONS: MappingProxyType[PlatformRole, frozenset[str]] = MappingProxyType(
    {
        PlatformRole.OWNER: ALL_PLATFORM_PERMISSIONS,
        PlatformRole.OPS_ADMIN: _READ_ONLY
        | {
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
        },
        PlatformRole.BILLING_ADMIN: _READ_ONLY
        | {"plans:write", "subscriptions:write", "deals:write", "billing:write"},
        PlatformRole.SUPPORT_AGENT: _READ_ONLY
        | {"support:break_glass", "tickets:write", "compliance:write"},
        PlatformRole.SECURITY_ANALYST: _READ_ONLY
        | {
            "blocks:write",
            "tenants:blacklist",
            "abuse:write",
            "dual_control:approve",
            "compliance:write",
        },
        PlatformRole.AUDITOR: _READ_ONLY,
        PlatformRole.ENGINEER_STAGING: _READ_ONLY
        | {"flags:write", "providers:write", "incidents:write"},
    }
)
