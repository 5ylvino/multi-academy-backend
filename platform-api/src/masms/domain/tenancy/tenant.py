"""The school tenant and its lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from masms.domain.shared.entity import AggregateRoot
from masms.domain.shared.errors import InvalidStateTransition, TenantUnavailable, ValidationFailed
from masms.domain.shared.ids import new_id
from masms.domain.shared.value_objects import Slug
from masms.domain.tenancy.events import TenantRegistered, TenantStatusChanged


class TenantStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDING = "offboarding"
    OFFBOARDED = "offboarded"


class SchoolLevel(StrEnum):
    NURSERY = "nursery"
    PRIMARY = "primary"
    SECONDARY = "secondary"


_TRANSITIONS: MappingProxyType[TenantStatus, frozenset[TenantStatus]] = MappingProxyType(
    {
        TenantStatus.PENDING_VERIFICATION: frozenset({TenantStatus.TRIAL, TenantStatus.OFFBOARDED}),
        TenantStatus.TRIAL: frozenset(
            {TenantStatus.ACTIVE, TenantStatus.SUSPENDED, TenantStatus.OFFBOARDING}
        ),
        TenantStatus.ACTIVE: frozenset({TenantStatus.SUSPENDED, TenantStatus.OFFBOARDING}),
        TenantStatus.SUSPENDED: frozenset(
            {TenantStatus.ACTIVE, TenantStatus.TRIAL, TenantStatus.OFFBOARDING}
        ),
        TenantStatus.OFFBOARDING: frozenset({TenantStatus.OFFBOARDED, TenantStatus.ACTIVE}),
        TenantStatus.OFFBOARDED: frozenset(),
    }
)

#: Statuses in which school users may sign in and use the product.
SERVING_STATUSES = frozenset({TenantStatus.TRIAL, TenantStatus.ACTIVE})


_COUNTRY_TIMEZONES = {
    "NG": "Africa/Lagos",
    "GH": "Africa/Accra",
    "KE": "Africa/Nairobi",
    "ZA": "Africa/Johannesburg",
    "RW": "Africa/Kigali",
    "UG": "Africa/Kampala",
    "GB": "Europe/London",
    "US": "America/New_York",
    "CA": "America/Toronto",
}


def default_timezone(country_code: str) -> str:
    return _COUNTRY_TIMEZONES.get(country_code.upper(), "UTC")


def validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationFailed(
            "Pick a valid time zone, e.g. Africa/Lagos.", details={"field": "timezone"}
        ) from exc


@dataclass(kw_only=True, eq=False)
class Tenant(AggregateRoot):
    slug: Slug
    name: str
    status: TenantStatus
    plan_code: str
    school_levels: frozenset[SchoolLevel]
    country_code: str
    contact_email: str
    created_at: datetime
    #: IANA zone. "Today" for registers, deadlines and reports is the school's local date.
    timezone: str = "Africa/Lagos"
    trial_ends_at: datetime | None = None
    status_reason: str | None = None
    #: Bumped on every runtime-policy change, so caches and tokens can detect staleness.
    config_version: int = 1

    @classmethod
    def register(
        cls,
        *,
        name: str,
        slug: Slug,
        school_levels: frozenset[SchoolLevel],
        country_code: str,
        contact_email: str,
        plan_code: str,
        now: datetime,
        timezone: str | None = None,
    ) -> Tenant:
        clean_name = " ".join(name.split())
        if not 2 <= len(clean_name) <= 160:
            raise ValidationFailed(
                "School name must be 2-160 characters.", details={"field": "name"}
            )
        if not school_levels:
            raise ValidationFailed(
                "Pick at least one school level.", details={"field": "school_levels"}
            )
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValidationFailed(
                "Country must be an ISO 3166 alpha-2 code.", details={"field": "country_code"}
            )
        zone = timezone or default_timezone(country_code)
        validate_timezone(zone)
        tenant = cls(
            id=new_id(),
            timezone=zone,
            slug=slug,
            name=clean_name,
            status=TenantStatus.PENDING_VERIFICATION,
            plan_code=plan_code,
            school_levels=school_levels,
            country_code=country_code.upper(),
            contact_email=contact_email,
            created_at=now,
        )
        tenant.record(
            TenantRegistered(tenant_id=tenant.id, slug=slug.value, school_name=clean_name)
        )
        return tenant

    def local_today(self, now: datetime) -> date:
        return now.astimezone(ZoneInfo(self.timezone)).date()

    @property
    def is_serving(self) -> bool:
        return self.status in SERVING_STATUSES

    def ensure_serving(self) -> None:
        if not self.is_serving:
            raise TenantUnavailable(
                "This school account is not available.", details={"status": self.status.value}
            )

    def transition(self, target: TenantStatus, *, reason: str | None = None) -> None:
        if target is self.status:
            return
        if target not in _TRANSITIONS[self.status]:
            raise InvalidStateTransition(
                f"A school cannot move from {self.status.value} to {target.value}.",
                details={"from": self.status.value, "to": target.value},
            )
        previous = self.status
        self.status = target
        self.status_reason = reason
        self.config_version += 1
        self.record(
            TenantStatusChanged(
                tenant_id=self.id, previous=previous.value, current=target.value, reason=reason
            )
        )

    def start_trial(self, now: datetime, length: timedelta) -> None:
        self.transition(TenantStatus.TRIAL)
        self.trial_ends_at = now + length

    def suspend(self, reason: str) -> None:
        if not reason.strip():
            raise ValidationFailed(
                "A reason is required to suspend a school.", details={"field": "reason"}
            )
        self.transition(TenantStatus.SUSPENDED, reason=reason.strip())

    def reinstate(self, now: datetime) -> None:
        in_trial = self.trial_ends_at is not None and self.trial_ends_at > now
        self.transition(TenantStatus.TRIAL if in_trial else TenantStatus.ACTIVE)

    def activate(self) -> None:
        self.transition(TenantStatus.ACTIVE)

    def change_plan(self, plan_code: str) -> None:
        if plan_code != self.plan_code:
            self.plan_code = plan_code
            self.config_version += 1
