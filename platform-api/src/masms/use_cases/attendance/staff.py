"""Staff check-in: settings, the gate screen code, checking in, and the daily staff register."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from masms.domain.attendance.staff import (
    KIOSK_STEP_SECONDS,
    AttendanceSettings,
    Site,
    StaffCheckIn,
    StaffDayStatus,
)
from masms.domain.identity import totp
from masms.domain.identity.roles import Realm, SchoolRole
from masms.domain.shared.errors import Conflict, PermissionDenied, ValidationFailed
from masms.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    RateLimiter,
    SchoolTime,
    Scope,
    SecretBox,
    SecretGenerator,
    UnitOfWork,
    UnitOfWorkFactory,
)
from masms.use_cases.shared.principal import Principal

#: Who checks in until the school changes it: everyone on staff except the proprietor.
DEFAULT_CHECK_IN_ROLES = frozenset(
    r.value
    for r in SchoolRole
    if r not in {SchoolRole.DIRECTOR, SchoolRole.STUDENT, SchoolRole.PARENT}
)
_STAFF_ROLES = DEFAULT_CHECK_IN_ROLES | {SchoolRole.DIRECTOR.value}
MAX_HISTORY_DAYS = 120


@dataclass(frozen=True, slots=True)
class SettingsView:
    window_opens: time
    late_after: time
    window_closes: time
    business_days: list[int]
    check_in_roles: list[str]
    sites: list[Site]
    require_location: bool

    @classmethod
    def of(cls, s: AttendanceSettings) -> SettingsView:
        return cls(
            window_opens=s.window_opens,
            late_after=s.late_after,
            window_closes=s.window_closes,
            business_days=sorted(s.business_days),
            check_in_roles=sorted(s.check_in_roles),
            sites=list(s.sites),
            require_location=s.require_location,
        )


@dataclass(frozen=True, slots=True)
class UpdateSettingsCommand:
    window_opens: time
    late_after: time
    window_closes: time
    business_days: list[int]
    check_in_roles: list[str]
    sites: list[Site]
    require_location: bool


@dataclass(frozen=True, slots=True)
class KioskCode:
    code: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class CheckInView:
    day: date
    status: str
    checked_in_at: datetime | None
    site_name: str | None


@dataclass(frozen=True, slots=True)
class StaffRegisterRow:
    staff_id: UUID
    full_name: str
    roles: list[str]
    status: str
    checked_in_at: datetime | None
    site_name: str | None


class StaffAttendance:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        school_time: SchoolTime,
        box: SecretBox,
        secrets: SecretGenerator,
        limiter: RateLimiter,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time
        self._box = box
        self._secrets = secrets
        self._limiter = limiter

    async def _settings(self, uow: UnitOfWork, tenant_id: UUID) -> AttendanceSettings:
        existing = await uow.attendance.get_settings()
        return existing or AttendanceSettings.defaults(
            tenant_id, check_in_roles=DEFAULT_CHECK_IN_ROLES
        )

    # ---- settings ------------------------------------------------------------------------

    async def get_settings(self, actor: Principal) -> SettingsView:
        actor.require_realm(Realm.SCHOOL)
        if not (
            actor.permissions.allows("organization_settings:configure")
            or actor.permissions.allows("staff_attendance:view_all")
        ):
            raise PermissionDenied("You don't have access to attendance settings.")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return SettingsView.of(await self._settings(uow, actor.school_tenant_id))

    async def update_settings(self, actor: Principal, cmd: UpdateSettingsCommand) -> SettingsView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("organization_settings:configure")
        unknown = set(cmd.check_in_roles) - _STAFF_ROLES
        if unknown:
            raise ValidationFailed(
                "Only staff roles can be asked to check in.",
                details={"roles": sorted(unknown)},
            )
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await self._settings(uow, tenant_id)
            settings.update(
                window_opens=cmd.window_opens,
                late_after=cmd.late_after,
                window_closes=cmd.window_closes,
                business_days=frozenset(cmd.business_days),
                check_in_roles=frozenset(cmd.check_in_roles),
                sites=tuple(cmd.sites),
                require_location=cmd.require_location,
            )
            await uow.attendance.save_settings(settings)
            uow.audit(
                AuditEntry(
                    action="attendance.settings_updated",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="attendance_settings",
                    target_id=str(tenant_id),
                    metadata={
                        "window": f"{cmd.window_opens}-{cmd.window_closes}",
                        "late_after": str(cmd.late_after),
                        "sites": len(cmd.sites),
                        "require_location": cmd.require_location,
                    },
                )
            )
            await uow.commit()
        return SettingsView.of(settings)

    # ---- gate screen ---------------------------------------------------------------------

    async def kiosk_code(self, actor: Principal) -> KioskCode:
        """The code the check-in screen shows. Changes every 60 seconds."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("staff_attendance:view_all")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await self._settings(uow, tenant_id)
            if settings.kiosk_secret_ciphertext is None:
                settings.kiosk_secret_ciphertext = self._box.encrypt(self._secrets.random_bytes(20))
                await uow.attendance.save_settings(settings)
                await uow.commit()
            secret = self._box.decrypt(settings.kiosk_secret_ciphertext)
        now = self._clock.now()
        step = totp.time_step(now, KIOSK_STEP_SECONDS)
        expires_in = KIOSK_STEP_SECONDS - int(now.timestamp()) % KIOSK_STEP_SECONDS
        return KioskCode(code=totp.code_at(secret, step), expires_in=expires_in)

    # ---- checking in ---------------------------------------------------------------------

    async def check_in(
        self, actor: Principal, *, code: str, latitude: float | None, longitude: float | None
    ) -> CheckInView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("staff_attendance:create")
        await self._limiter.hit(f"checkin:user:{actor.user_id}", limit=10, window_seconds=300)
        tenant_id = actor.school_tenant_id
        local_now = await self._school_time.now_local(tenant_id)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            settings = await self._settings(uow, tenant_id)
            if not settings.applies_to(actor.roles):
                raise ValidationFailed(
                    "Daily check-in isn't required for your role.", code="check_in_not_required"
                )
            status = settings.check_in_status(local_now)
            if settings.kiosk_secret_ciphertext is None or (
                totp.matching_step(
                    self._box.decrypt(settings.kiosk_secret_ciphertext),
                    code.strip(),
                    self._clock.now(),
                    after_step=None,
                    period=KIOSK_STEP_SECONDS,
                )
                is None
            ):
                raise ValidationFailed(
                    "That code didn't match the check-in screen. Enter the code shown now.",
                    code="check_in_code_invalid",
                )
            site = settings.match_site(latitude, longitude)
            check_in = StaffCheckIn.record(
                tenant_id=tenant_id,
                staff_id=actor.user_id,
                day=local_now.date(),
                status=status,
                now=self._clock.now(),
                site_name=site,
            )
            if not await uow.attendance.add_check_in(check_in):
                raise Conflict("You've already checked in today.", code="already_checked_in")
            await uow.commit()
        return CheckInView(
            day=check_in.day,
            status=check_in.status.value,
            checked_in_at=check_in.checked_in_at,
            site_name=site,
        )

    # ---- registers -----------------------------------------------------------------------

    async def day_register(self, actor: Principal, day: date | None) -> list[StaffRegisterRow]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("staff_attendance:view_all")
        tenant_id = actor.school_tenant_id
        local_now = await self._school_time.now_local(tenant_id)
        day = day or local_now.date()
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            settings = await self._settings(uow, tenant_id)
            staff = await uow.users.school_users_with_roles(tenant_id, settings.check_in_roles)
            check_ins = {c.staff_id: c for c in await uow.attendance.check_ins_for_day(day)}
        rows = []
        for person in staff:
            found = check_ins.get(person.id)
            rows.append(
                StaffRegisterRow(
                    staff_id=person.id,
                    full_name=person.full_name,
                    roles=sorted(person.roles),
                    status=settings.day_status(
                        day=day,
                        local_now=local_now,
                        checked_in=found.status if found else None,
                    ).value,
                    checked_in_at=found.checked_in_at if found else None,
                    site_name=found.site_name if found else None,
                )
            )
        return rows

    async def my_history(
        self, actor: Principal, *, start: date | None, end: date | None
    ) -> list[CheckInView]:
        actor.require_realm(Realm.SCHOOL)
        if not (
            actor.permissions.allows("staff_attendance:read")
            or actor.permissions.allows("staff_attendance:create")
        ):
            raise PermissionDenied("You don't have access to staff attendance.")
        tenant_id = actor.school_tenant_id
        local_now = await self._school_time.now_local(tenant_id)
        end = end or local_now.date()
        start = start or end - timedelta(days=30)
        if end < start or (end - start).days > MAX_HISTORY_DAYS:
            raise ValidationFailed("Pick a range of up to 120 days.", details={"field": "range"})
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            settings = await self._settings(uow, tenant_id)
            found = {
                c.day: c
                for c in await uow.attendance.check_ins_for_staff(actor.user_id, start, end)
            }
        views = []
        day = end
        while day >= start:
            check_in = found.get(day)
            status = settings.day_status(
                day=day, local_now=local_now, checked_in=check_in.status if check_in else None
            )
            if status is not StaffDayStatus.NOT_REQUIRED:
                views.append(
                    CheckInView(
                        day=day,
                        status=status.value,
                        checked_in_at=check_in.checked_in_at if check_in else None,
                        site_name=check_in.site_name if check_in else None,
                    )
                )
            day -= timedelta(days=1)
        return views
