"""Feature flags and how a flag resolves for one school.

Resolution order (first match wins):

1. Kill switch on the flag turns it off everywhere.
2. A per-school override turns it on or off for that school.
3. A percentage rollout includes a stable slice of schools.
4. The school's plan includes the feature.
5. The flag's global default.

Unknown flags resolve to off (fail closed).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.shared.errors import ValidationFailed

_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}$")


def validate_flag_key(key: str) -> str:
    if not _KEY.match(key) or len(key) > 100:
        raise ValidationFailed(
            "Flag keys look like 'fees.installments': lowercase words joined by dots.",
            details={"field": "key"},
        )
    return key


@dataclass(kw_only=True)
class FeatureFlag:
    key: str
    name: str
    description: str = ""
    default_enabled: bool = False
    kill_switch: bool = False
    rollout_percent: int = 0
    updated_at: datetime
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        validate_flag_key(self.key)
        if not 0 <= self.rollout_percent <= 100:
            raise ValidationFailed(
                "Rollout must be between 0 and 100.", details={"field": "rollout_percent"}
            )

    def in_rollout(self, tenant_id: UUID) -> bool:
        if self.rollout_percent <= 0:
            return False
        if self.rollout_percent >= 100:
            return True
        digest = hashlib.sha256(f"{self.key}:{tenant_id}".encode()).digest()
        bucket = int.from_bytes(digest[:2], "big") % 100
        return bucket < self.rollout_percent


@dataclass(kw_only=True)
class Plan:
    code: str
    name: str
    features: frozenset[str] = field(default_factory=frozenset)
    student_limit: int | None = None
    active: bool = True
    #: What a school pays the platform each billing period, in minor units:
    #: ``base_price_minor + price_per_student_minor * active students``. Zero means free.
    currency: str = "NGN"
    base_price_minor: int = 0
    price_per_student_minor: int = 0
    #: Length of a billing period. 4 months matches a school term.
    billing_interval_months: int = 4

    @property
    def is_free(self) -> bool:
        return self.base_price_minor == 0 and self.price_per_student_minor == 0


@dataclass(frozen=True, slots=True)
class FlagContext:
    tenant_id: UUID
    plan: Plan | None
    overrides: Mapping[str, bool]


def resolve_flag(flag: FeatureFlag | None, ctx: FlagContext) -> bool:
    if flag is None or flag.kill_switch:
        return False
    if flag.key in ctx.overrides:
        return ctx.overrides[flag.key]
    if flag.in_rollout(ctx.tenant_id):
        return True
    if ctx.plan is not None and flag.key in ctx.plan.features:
        return True
    return flag.default_enabled


def resolve_all(flags: Iterable[FeatureFlag], ctx: FlagContext) -> dict[str, bool]:
    return {flag.key: resolve_flag(flag, ctx) for flag in flags}
