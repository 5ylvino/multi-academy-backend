"""Staff daily check-in: proof of presence on campus during the school's check-in window.

Presence is proved two ways at once:
- a rotating 6-digit code shown on the school's check-in screen at the gate (it changes
  every 60 seconds, so a code sent by phone to someone at home is useless a minute later);
- the device's location inside one of the school's sites, when the school requires it.
A missing check-in after the window closes means absent; nothing is written for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID

from app.domain.shared.entity import Entity
from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id

KIOSK_STEP_SECONDS = 60
EARTH_RADIUS_M = 6_371_000


class CheckInStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"


class StaffDayStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    PENDING = "pending"  # window still open and no check-in yet
    NOT_REQUIRED = "not_required"  # not a business day


@dataclass(frozen=True, slots=True)
class Site:
    name: str
    latitude: float
    longitude: float
    radius_m: int

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 80:
            raise ValidationFailed("Each site needs a name up to 80 characters.")
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValidationFailed("Site coordinates are not valid.")
        if not 25 <= self.radius_m <= 2000:
            raise ValidationFailed("Site radius must be between 25 and 2,000 metres.")

    def distance_m(self, latitude: float, longitude: float) -> float:
        """Great-circle distance (haversine)."""
        p1, p2 = math.radians(self.latitude), math.radians(latitude)
        dp = p2 - p1
        dl = math.radians(longitude - self.longitude)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.distance_m(latitude, longitude) <= self.radius_m


@dataclass(kw_only=True, eq=False)
class AttendanceSettings(Entity):
    """One per school. ``id`` equals the tenant id."""

    tenant_id: UUID
    window_opens: time = time(6, 30)
    late_after: time = time(8, 0)
    window_closes: time = time(10, 0)
    #: ISO weekdays, Monday = 1.
    business_days: frozenset[int] = frozenset({1, 2, 3, 4, 5})
    check_in_roles: frozenset[str] = field(default_factory=frozenset)
    sites: tuple[Site, ...] = ()
    require_location: bool = False
    kiosk_secret_ciphertext: str | None = None
    version: int = 1

    @classmethod
    def defaults(cls, tenant_id: UUID, *, check_in_roles: frozenset[str]) -> AttendanceSettings:
        return cls(id=tenant_id, tenant_id=tenant_id, check_in_roles=check_in_roles)

    def update(
        self,
        *,
        window_opens: time,
        late_after: time,
        window_closes: time,
        business_days: frozenset[int],
        check_in_roles: frozenset[str],
        sites: tuple[Site, ...],
        require_location: bool,
    ) -> None:
        if not window_opens < late_after <= window_closes:
            raise ValidationFailed(
                "Opening time must be before the late time, and the late time no later than "
                "closing.",
                details={"field": "window"},
            )
        if not business_days or not business_days <= frozenset(range(1, 8)):
            raise ValidationFailed("Pick at least one school day.", details={"field": "days"})
        if require_location and not sites:
            raise ValidationFailed(
                "Add at least one site before requiring location.", details={"field": "sites"}
            )
        if len(sites) > 20:
            raise ValidationFailed("A school can have up to 20 sites.", details={"field": "sites"})
        self.window_opens = window_opens
        self.late_after = late_after
        self.window_closes = window_closes
        self.business_days = business_days
        self.check_in_roles = check_in_roles
        self.sites = sites
        self.require_location = require_location

    def is_business_day(self, day: date) -> bool:
        return day.isoweekday() in self.business_days

    def applies_to(self, roles: frozenset[str]) -> bool:
        return bool(roles & self.check_in_roles)

    def day_status(
        self, *, day: date, local_now: datetime, checked_in: CheckInStatus | None
    ) -> StaffDayStatus:
        if checked_in is not None:
            return StaffDayStatus(checked_in.value)
        if not self.is_business_day(day):
            return StaffDayStatus.NOT_REQUIRED
        if day > local_now.date():
            return StaffDayStatus.PENDING
        if day == local_now.date() and local_now.time() < self.window_closes:
            return StaffDayStatus.PENDING
        return StaffDayStatus.ABSENT

    def check_in_status(self, local_now: datetime) -> CheckInStatus:
        """Present or late, or an error when outside the window or not a school day."""
        if not self.is_business_day(local_now.date()):
            raise ValidationFailed("Check-in is only open on school days.", code="not_school_day")
        now = local_now.time()
        if not self.window_opens <= now <= self.window_closes:
            raise ValidationFailed(
                f"Check-in is open from {self.window_opens:%H:%M} to {self.window_closes:%H:%M}.",
                code="check_in_closed",
            )
        return CheckInStatus.LATE if now > self.late_after else CheckInStatus.PRESENT

    def match_site(self, latitude: float | None, longitude: float | None) -> str | None:
        """The site the device is in. Required when the school asks for location."""
        if latitude is not None and longitude is not None:
            for site in self.sites:
                if site.contains(latitude, longitude):
                    return site.name
        if self.require_location:
            if latitude is None or longitude is None:
                raise ValidationFailed(
                    "Turn on location for this check-in.", code="location_required"
                )
            raise ValidationFailed(
                "You're not at a school site. Move closer and try again.", code="outside_site"
            )
        return None


@dataclass(kw_only=True, eq=False)
class StaffCheckIn(Entity):
    tenant_id: UUID
    staff_id: UUID
    day: date
    status: CheckInStatus
    checked_in_at: datetime
    site_name: str | None  # we keep the matched site only, never raw coordinates

    @classmethod
    def record(
        cls,
        *,
        tenant_id: UUID,
        staff_id: UUID,
        day: date,
        status: CheckInStatus,
        now: datetime,
        site_name: str | None,
    ) -> StaffCheckIn:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            staff_id=staff_id,
            day=day,
            status=status,
            checked_in_at=now,
            site_name=site_name,
        )
