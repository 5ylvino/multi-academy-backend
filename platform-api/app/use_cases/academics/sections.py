"""Sections of a school: who leads which levels, and what each person's academic work covers.

Scope rules (applied by every academic use case through :func:`scope_of`):

- Whole-school leaders (director, school admin, IT admin) cover the whole school.
- Staff assigned to sections cover the levels of those sections and nothing else.
- Staff holding a section role (heads, teachers) who aren't assigned yet cover nothing.
- Other staff (bursar, nurse, guard, driver, office staff not in a section) serve the whole
  school; their permissions already limit what they can do.

Role fit: the principal and vice principal head sections that include secondary; the head
teacher and assistant head teacher head sections that include nursery or primary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.academics.sections import (
    FEATURE_NAMES,
    Section,
    SectionFeature,
    SectionScope,
    check_arrangement,
    default_arrangement,
    default_name,
    ordered,
    parse_features,
)
from app.domain.academics.structure import SchoolLevelCode
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import (
    Conflict,
    FeatureDisabled,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.use_cases.shared import audit
from app.use_cases.shared.ports import Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

WHOLE_SCHOOL_ROLES = frozenset(
    {SchoolRole.DIRECTOR.value, SchoolRole.SCHOOL_ADMIN.value, SchoolRole.IT_ADMIN.value}
)
SECONDARY_HEADS = frozenset({SchoolRole.PRINCIPAL.value, SchoolRole.VICE_PRINCIPAL.value})
BASIC_HEADS = frozenset({SchoolRole.HEAD_TEACHER.value, SchoolRole.ASSISTANT_HEAD_TEACHER.value})
#: Roles whose work belongs to a section. Holders must be assigned to at least one.
SECTION_ROLES = (
    SECONDARY_HEADS
    | BASIC_HEADS
    | frozenset({SchoolRole.CLASS_TEACHER.value, SchoolRole.SUBJECT_TEACHER.value})
)
FAMILY_ROLES = frozenset({SchoolRole.STUDENT.value, SchoolRole.PARENT.value})
#: Service roles the owner chooses to run for the whole school (default) or per section.
SERVICE_ROLES = frozenset(
    {
        SchoolRole.BURSAR.value,
        SchoolRole.NURSE.value,
        SchoolRole.GUARD.value,
        SchoolRole.DRIVER.value,
        SchoolRole.ADMINISTRATIVE_STAFF.value,
    }
)


def check_service_roles(roles: Iterable[str]) -> set[str]:
    chosen = set(roles)
    unknown = chosen - SERVICE_ROLES
    if unknown:
        raise ValidationFailed(
            "Only the bursar, nurse, guard, driver and office staff can work per section.",
            details={"field": "per_section_roles", "unknown": sorted(unknown)},
        )
    return chosen


async def sectioned_roles(uow: UnitOfWork) -> frozenset[str]:
    """Roles whose holders work in sections at this school."""
    return SECTION_ROLES | frozenset(await uow.sections.per_section_roles())


ARRANGE = "school_levels:manage"
ASSIGN = "staff:update"


async def scope_of(uow: UnitOfWork, actor: Principal) -> SectionScope:
    """The part of the school this person's academic work covers."""
    return await scope_for(uow, actor.user_id, actor.roles)


async def scope_for(uow: UnitOfWork, user_id: UUID, roles: Iterable[str]) -> SectionScope:
    held = frozenset(roles)
    if held & WHOLE_SCHOOL_ROLES:
        return SectionScope.everything()
    assigned = await uow.sections.sections_of(user_id)
    if not assigned:
        if held & (await sectioned_roles(uow) | FAMILY_ROLES):
            return SectionScope(whole_school=False, section_ids=frozenset(), levels=frozenset())
        return SectionScope.everything()
    sections = [s for s in await uow.sections.list_sections() if s.id in assigned]
    return SectionScope(
        whole_school=False,
        section_ids=frozenset(s.id for s in sections),
        levels=frozenset(lvl.value for s in sections for lvl in s.levels),
    )


async def student_level(uow: UnitOfWork, student_id: UUID) -> str | None:
    """The level of the class the student is in this session, if any."""
    term = await uow.academics.current_term()
    if term is None:
        return None
    class_id = await uow.academics.class_of_student(student_id, term.session_id)
    if class_id is None:
        return None
    school_class = await uow.academics.get_class(class_id)
    return school_class.school_level.value if school_class else None


async def ensure_student_in_scope(uow: UnitOfWork, actor: Principal, student_id: UUID) -> None:
    """Section staff reach only students in their section's classes."""
    scope = await scope_of(uow, actor)
    if scope.whole_school:
        return
    level = await student_level(uow, student_id)
    if level is None or not scope.covers(level):
        raise PermissionDenied(
            "This student is in another section of the school.", code="other_section"
        )


async def require_feature(
    uow: UnitOfWork, level: SchoolLevelCode | str, feature: SectionFeature
) -> None:
    """The section that holds this level must have the feature switched on."""
    value = level.value if isinstance(level, SchoolLevelCode) else level
    for section in await uow.sections.list_sections():
        if value in {lvl.value for lvl in section.levels}:
            if not section.offers(feature):
                raise FeatureDisabled(
                    f"{FEATURE_NAMES[feature]} isn't used in {section.name}.",
                    code="section_feature_off",
                    details={"feature": feature.value, "section_id": str(section.id)},
                )
            return


async def require_student_feature(
    uow: UnitOfWork, student_id: UUID, feature: SectionFeature
) -> None:
    """For a student in a class this session, their section must have the feature on."""
    level = await student_level(uow, student_id)
    if level is not None:
        await require_feature(uow, level, feature)


async def serve_student(
    uow: UnitOfWork, actor: Principal, student_id: UUID, feature: SectionFeature
) -> None:
    """Staff acting for a student: the student's section must use the feature, and staff who
    work per section reach only their own section's students."""
    await require_student_feature(uow, student_id, feature)
    await ensure_student_in_scope(uow, actor, student_id)


async def visible_students(uow: UnitOfWork, actor: Principal, student_ids: set[UUID]) -> set[UUID]:
    """The students of a list this person may see."""
    scope = await scope_of(uow, actor)
    if scope.whole_school:
        return set(student_ids)
    seen = set()
    for student_id in student_ids:
        level = await student_level(uow, student_id)
        if level is not None and scope.covers(level):
            seen.add(student_id)
    return seen


async def ensure_works_in(uow: UnitOfWork, user_id: UUID, level: str, field: str) -> None:
    """A teacher given work in a class must work in that class's section."""
    user = await uow.users.get(user_id)
    if user is None:
        raise ValidationFailed("Pick a teacher from your school.", details={"field": field})
    scope = await scope_for(uow, user_id, user.roles)
    if not scope.covers(level):
        raise ValidationFailed(
            f"{user.full_name} doesn't work in this class's section.",
            details={"field": field},
            code="teacher_other_section",
        )


def check_fit(roles: Iterable[str], sections: Iterable[Section]) -> None:
    """A principal heads secondary; a head teacher heads nursery or primary."""
    held = set(roles)
    secondary_only = bool(held & SECONDARY_HEADS) and not held & BASIC_HEADS
    basic_only = bool(held & BASIC_HEADS) and not held & SECONDARY_HEADS
    for section in sections:
        if secondary_only and not section.has_secondary:
            raise ValidationFailed(
                f"A principal or vice principal leads secondary, not {section.name}.",
                details={"field": "section_ids"},
                code="role_section_mismatch",
            )
        if basic_only and not section.has_basic:
            raise ValidationFailed(
                f"A head teacher leads nursery or primary, not {section.name}.",
                details={"field": "section_ids"},
                code="role_section_mismatch",
            )


async def resolve_assignment(
    uow: UnitOfWork, roles: Iterable[str], section_ids: Iterable[UUID] | None
) -> list[Section]:
    """The sections a new or changed staff member works in. A school with one section puts
    section staff there without asking."""
    held = set(roles)
    sections = await uow.sections.list_sections()
    by_id = {s.id: s for s in sections}
    wanted = set(section_ids or [])
    unknown = wanted - by_id.keys()
    if unknown:
        raise ValidationFailed("Pick sections of your school.", details={"field": "section_ids"})
    if held & FAMILY_ROLES:
        if wanted:
            raise ValidationFailed(
                "Students and parents aren't assigned to sections; students follow their class.",
                details={"field": "section_ids"},
            )
        return []
    sectioned = await sectioned_roles(uow)
    if wanted and not held & sectioned:
        raise ValidationFailed(
            "This person serves the whole school, so they aren't placed in a section.",
            details={"field": "section_ids"},
            code="whole_school_staff",
        )
    if not wanted and held & sectioned:
        if len(sections) == 1:
            wanted = {sections[0].id}
        else:
            raise ValidationFailed(
                "Pick the section this person works in.",
                details={"field": "section_ids"},
                code="section_required",
            )
    chosen = [by_id[s] for s in wanted]
    check_fit(held, chosen)
    return chosen


async def create_default_sections(
    uow: UnitOfWork,
    *,
    tenant_id: UUID,
    levels: Iterable[SchoolLevelCode],
    nursery_with_primary: bool,
    names: dict[frozenset[SchoolLevelCode], str] | None,
    now: datetime,
) -> list[Section]:
    groups = default_arrangement(levels, nursery_with_primary=nursery_with_primary)
    sections = [
        Section.create(tenant_id=tenant_id, levels=g, name=(names or {}).get(g), now=now)
        for g in groups
    ]
    await uow.sections.save_arrangement(sections, removed=[])
    return sections


# ---- views ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionStaffView:
    user_id: UUID
    full_name: str
    roles: list[str]


@dataclass(frozen=True, slots=True)
class SectionView:
    id: UUID
    name: str
    code: str
    levels: list[str]
    heads: list[SectionStaffView]
    staff_count: int
    features: list[str]


@dataclass(frozen=True, slots=True)
class SectionPlan:
    """One section in a requested arrangement. ``id`` keeps an existing section."""

    id: UUID | None
    name: str | None
    levels: list[str]


def _levels(values: Iterable[str]) -> frozenset[SchoolLevelCode]:
    try:
        return frozenset(SchoolLevelCode(v) for v in values)
    except ValueError as exc:
        raise ValidationFailed("Unknown school level.", details={"field": "levels"}) from exc


class Sections:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def list_all(self, actor: Principal) -> list[SectionView]:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return await self._views(uow)

    async def mine(self, actor: Principal) -> tuple[bool, list[SectionView]]:
        """Whether the person covers the whole school, and the sections they work in."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            scope = await scope_of(uow, actor)
            views = await self._views(uow)
        if scope.whole_school:
            return True, views
        return False, [v for v in views if v.id in scope.section_ids]

    async def arrange(self, actor: Principal, plans: list[SectionPlan]) -> list[SectionView]:
        """Re-group the school's levels: split nursery off primary, merge them, rename.

        Classes, subjects and students follow their level. Staff of a removed section move to
        the sections that now hold its levels, so nobody loses their work.
        """
        actor.require_realm(Realm.SCHOOL)
        actor.require(ARRANGE)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.sections.lock()
            tenant = await uow.tenants.get(tenant_id)
            if tenant is None:
                raise NotFound("School not found.")
            offered = frozenset(SchoolLevelCode(lvl.value) for lvl in tenant.school_levels)
            groups = [_levels(p.levels) for p in plans]
            check_arrangement(groups, offered)
            current = {s.id: s for s in await uow.sections.list_sections()}
            kept_ids = [p.id for p in plans if p.id is not None]
            if len(set(kept_ids)) != len(kept_ids) or set(kept_ids) - current.keys():
                raise ValidationFailed("Pick sections of your school.", details={"field": "id"})
            before = {
                s.id: (s.name, sorted(lvl.value for lvl in s.levels)) for s in current.values()
            }
            result: list[Section] = []
            for plan, group in zip(plans, groups, strict=True):
                if plan.id is None:
                    result.append(
                        Section.create(tenant_id=tenant_id, levels=group, name=plan.name, now=now)
                    )
                    continue
                section = current[plan.id]
                section.regroup(group)
                if plan.name:
                    section.rename(plan.name)
                elif sorted(lvl.value for lvl in group) != before[section.id][1]:
                    section.rename(default_name(group))
                result.append(section)
            removed = [sid for sid in current if sid not in kept_ids]
            moving = {sid: await uow.sections.release_staff(sid) for sid in removed}
            await uow.sections.save_arrangement(result, removed=removed)
            for sid, people in moving.items():
                takers = {s.id for s in result if s.levels & current[sid].levels}
                await uow.sections.add_staff(takers, people, by=actor.user_id, now=now)
            # Heads must still fit the sections they are in.
            for section in result:
                staff = await uow.sections.staff_of(section.id)
                for user_id in staff:
                    user = await uow.users.get(user_id)
                    if user is not None:
                        check_fit(user.roles, [section])
            uow.audit(
                audit.by(
                    actor,
                    "academics.sections_arranged",
                    target_type="school",
                    target_id=tenant_id,
                    before=[{"name": n, "levels": lv} for n, lv in before.values()],
                    after=[
                        {"name": s.name, "levels": [lvl.value for lvl in ordered(s.levels)]}
                        for s in result
                    ],
                )
            )
            await uow.commit()
        return await self.list_all(actor)

    async def assign(
        self, actor: Principal, user_id: UUID, section_ids: list[UUID]
    ) -> list[SectionView]:
        """Set the sections a staff member works in."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(ASSIGN)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.sections.lock()
            user = await uow.users.get(user_id)
            if user is None or user.tenant_id != tenant_id:
                raise NotFound("Staff member not found.")
            if user.roles & WHOLE_SCHOOL_ROLES:
                raise Conflict(
                    "Whole-school leaders cover every section already.", code="whole_school_role"
                )
            chosen = await resolve_assignment(uow, user.roles, section_ids)
            before = await uow.sections.sections_of(user_id)
            after = {s.id for s in chosen}
            await uow.sections.assign(user_id, after, by=actor.user_id, now=now)
            if before != after:
                uow.audit(
                    audit.by(
                        actor,
                        "academics.staff_sections_set",
                        target_type="school_user",
                        target_id=user_id,
                        sections=sorted(s.name for s in chosen),
                    )
                )
            await uow.commit()  # audit: written above when the sections changed.
        return [v for v in await self.list_all(actor) if v.id in after]

    async def set_features(
        self, actor: Principal, section_id: UUID, features: list[str]
    ) -> list[SectionView]:
        """Switch a section's features on or off. Data already recorded is kept."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(ARRANGE)
        chosen = parse_features(features)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.sections.lock()
            sections = await uow.sections.list_sections()
            section = next((s for s in sections if s.id == section_id), None)
            if section is None:
                raise NotFound("Section not found.")
            before = section.features
            section.set_features(chosen)
            await uow.sections.save_arrangement(sections, removed=[])
            if before != chosen:
                uow.audit(
                    audit.by(
                        actor,
                        "academics.section_features_set",
                        target_type="section",
                        target_id=section.id,
                        section=section.name,
                        turned_on=sorted(f.value for f in chosen - before),
                        turned_off=sorted(f.value for f in before - chosen),
                    )
                )
            await uow.commit()  # audit: written above when the features changed.
        return await self.list_all(actor)

    async def staff_coverage(self, actor: Principal) -> list[str]:
        """Service roles this school runs per section."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return sorted(await uow.sections.per_section_roles())

    async def set_staff_coverage(self, actor: Principal, roles: list[str]) -> list[str]:
        """Choose which service roles work per section. People in a role moving to the whole
        school leave their sections; people in a role moving to sections are placed in the
        only section, or must be given one (the answer lists who)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(ARRANGE)
        chosen = check_service_roles(roles)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await uow.sections.lock()
            before = await uow.sections.per_section_roles()
            await uow.sections.set_per_section_roles(chosen)
            sections = await uow.sections.list_sections()
            changed = before ^ chosen
            for role in sorted(changed):
                page_cursor: str | None = None
                while True:
                    page = await uow.users.list_school_users(
                        tenant_id, role=role, search=None, cursor=page_cursor, limit=200
                    )
                    for user in page.items:
                        if user.roles & SECTION_ROLES:
                            continue  # also a head or teacher: stays where they are
                        if role in chosen and len(sections) == 1:
                            await uow.sections.assign(
                                user.id, {sections[0].id}, by=actor.user_id, now=now
                            )
                        elif role not in chosen and not user.roles & chosen:
                            await uow.sections.assign(user.id, set(), by=actor.user_id, now=now)
                    if page.next_cursor is None:
                        break
                    page_cursor = page.next_cursor
            if changed:
                uow.audit(
                    audit.by(
                        actor,
                        "academics.staff_coverage_set",
                        target_type="school",
                        target_id=tenant_id,
                        per_section=sorted(chosen),
                        whole_school=sorted(SERVICE_ROLES - chosen),
                    )
                )
            await uow.commit()  # audit: written above when the choice changed.
        return sorted(chosen)

    async def _views(self, uow: UnitOfWork) -> list[SectionView]:
        sections = await uow.sections.list_sections()
        staff = {s.id: await uow.sections.staff_of(s.id) for s in sections}
        everyone: set[UUID] = set().union(*staff.values()) if staff else set()
        users = {u: await uow.users.get(u) for u in everyone}
        views = []
        for s in sections:
            heads = []
            for user_id in staff[s.id]:
                user = users.get(user_id)
                if user is not None and user.roles & (SECONDARY_HEADS | BASIC_HEADS):
                    heads.append(
                        SectionStaffView(
                            user_id=user_id,
                            full_name=user.full_name,
                            roles=sorted(user.roles & (SECONDARY_HEADS | BASIC_HEADS)),
                        )
                    )
            views.append(
                SectionView(
                    id=s.id,
                    name=s.name,
                    code=s.code,
                    levels=[lvl.value for lvl in ordered(s.levels)],
                    heads=sorted(heads, key=lambda h: h.full_name),
                    staff_count=len(staff[s.id]),
                    features=sorted(f.value for f in s.features),
                )
            )
        return views
