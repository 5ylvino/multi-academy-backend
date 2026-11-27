"""The assessment scheme and grading scale for each school level.

Anyone at the school can read them (they appear on report cards). Staff with ``grading:manage``
change them. A level's scheme is fixed once marks are recorded for it in the current term, so a
term's marks are always out of the same parts.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.academics.structure import SchoolLevelCode
from app.domain.assessment.scheme import (
    DEFAULT_SCALE,
    DEFAULT_SCHEME,
    Band,
    Component,
    ComponentKind,
    ComponentSpec,
    check_scale,
    check_scheme,
    new_component,
)
from app.domain.identity.roles import Realm
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.use_cases.shared import audit
from app.use_cases.shared.ports import Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

MANAGE = "grading:manage"
READ = "results:read"


@dataclass(frozen=True, slots=True)
class ComponentView:
    id: UUID
    code: str
    name: str
    kind: str
    max_score: int


@dataclass(frozen=True, slots=True)
class SchemeView:
    school_level: str
    components: list[ComponentView]
    locked: bool  # marks exist this term, so the scheme can't change until next term


@dataclass(frozen=True, slots=True)
class BandView:
    grade: str
    remark: str
    min_score: int
    max_score: int


def level_of(value: str) -> str:
    try:
        return SchoolLevelCode(value).value
    except ValueError as exc:
        raise ValidationFailed("Unknown school level.", details={"field": "school_level"}) from exc


async def scheme_for(uow: UnitOfWork, tenant_id: UUID, level: str) -> list[Component]:
    """The level's active parts, creating the default scheme the first time it is needed."""
    found = await uow.assessment.components(level)
    if found:
        return found
    if await uow.assessment.components(level, include_inactive=True):  # pragma: no cover
        raise InvalidStateTransition("Set up the assessment scheme first.", code="no_scheme")
    made = [
        new_component(tenant_id=tenant_id, school_level=level, spec=spec, position=i)
        for i, spec in enumerate(DEFAULT_SCHEME, start=1)
    ]
    for component in made:
        await uow.assessment.add_component(component)
    return made


async def scale_for(uow: UnitOfWork, level: str) -> list[Band]:
    return await uow.assessment.scale(level) or list(DEFAULT_SCALE)


class AssessmentSetup:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def scheme(self, actor: Principal, school_level: str) -> SchemeView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(READ)
        level = level_of(school_level)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            components = await scheme_for(uow, tenant_id, level)
            locked = await _locked(uow, level)
            await uow.commit()  # audit: not needed. Saves the default scheme on first use.
        return SchemeView(
            school_level=level, components=[_view(c) for c in components], locked=locked
        )

    async def save_scheme(
        self, actor: Principal, school_level: str, specs: list[ComponentSpec]
    ) -> SchemeView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        level = level_of(school_level)
        clean = check_scheme(specs)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            if await _locked(uow, level):
                raise InvalidStateTransition(
                    "Marks are already recorded this term, so the parts can't change until "
                    "next term.",
                    code="scheme_in_use",
                )
            existing = {
                c.code: c for c in await uow.assessment.components(level, include_inactive=True)
            }
            before = [c.code for c in existing.values() if c.active]
            for position, spec in enumerate(clean, start=1):
                current = existing.pop(spec.code, None)
                if current is None:
                    await uow.assessment.add_component(
                        new_component(
                            tenant_id=tenant_id, school_level=level, spec=spec, position=position
                        )
                    )
                    continue
                current.name = spec.name
                current.kind = spec.kind
                current.max_score = spec.max_score
                current.position = position
                current.active = True
                await uow.assessment.save_component(current)
            for retired in existing.values():  # kept for past terms' marks
                if retired.active:
                    retired.active = False
                    await uow.assessment.save_component(retired)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.scheme_changed",
                    target_type="school_level",
                    target_id=level,
                    before=before,
                    after=[f"{s.code}:{s.max_score}" for s in clean],
                )
            )
            components = await uow.assessment.components(level)
            await uow.commit()
        return SchemeView(
            school_level=level, components=[_view(c) for c in components], locked=False
        )

    async def scale(self, actor: Principal, school_level: str) -> list[BandView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require(READ)
        level = level_of(school_level)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            bands = await scale_for(uow, level)
        return [_band(b) for b in bands]

    async def save_scale(
        self, actor: Principal, school_level: str, bands: list[Band]
    ) -> list[BandView]:
        """Applies to results not yet released; released report cards keep their grades."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        level = level_of(school_level)
        clean = check_scale(bands)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            before = await uow.assessment.scale(level)
            await uow.assessment.replace_scale(level, clean)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.scale_changed",
                    target_type="school_level",
                    target_id=level,
                    before=[f"{b.grade}:{b.min_score}-{b.max_score}" for b in before],
                    after=[f"{b.grade}:{b.min_score}-{b.max_score}" for b in clean],
                )
            )
            await uow.commit()
        return [_band(b) for b in clean]


async def _locked(uow: UnitOfWork, level: str) -> bool:
    term = await uow.academics.current_term()
    return term is not None and await uow.assessment.level_has_marks(level, term.id)


def _view(c: Component) -> ComponentView:
    return ComponentView(
        id=c.id, code=c.code, name=c.name, kind=c.kind.value, max_score=c.max_score
    )


def _band(b: Band) -> BandView:
    return BandView(grade=b.grade, remark=b.remark, min_score=b.min_score, max_score=b.max_score)


__all__ = [
    "AssessmentSetup",
    "BandView",
    "ComponentKind",
    "ComponentView",
    "SchemeView",
    "level_of",
    "scale_for",
    "scheme_for",
]
