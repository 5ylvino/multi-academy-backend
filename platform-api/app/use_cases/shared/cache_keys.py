"""Cache key names in one place, so invalidation and reads always agree."""

from __future__ import annotations

from uuid import UUID

FLAGS_VERSION = "flags"


def tenant_runtime(tenant_id: UUID) -> str:
    return f"tenant:{tenant_id}:runtime"


def tenant_features(tenant_id: UUID, config_version: int, flags_version: int) -> str:
    return f"tenant:{tenant_id}:features:c{config_version}:f{flags_version}"
