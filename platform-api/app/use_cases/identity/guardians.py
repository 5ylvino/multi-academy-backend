"""Link parents to students, and let parents see their own children."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.identity.guardians import GuardianLink, parse_relationship
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.identity.user import UserAccount
from app.domain.shared.errors import Conflict, NotFound, PermissionDenied
from app.use_cases.shared.ports import AuditEntry, Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class GuardianView:
    parent_id: UUID
    student_id: UUID
    parent_name: str
    student_name: str
    relationship: str
    created_at: datetime


async def _school_user(uow: UnitOfWork, tenant_id: UUID, user_id: UUID) -> UserAccount:
    user = await uow.users.get(user_id)
    if user is None or user.realm is not Realm.SCHOOL or user.tenant_id != tenant_id:
        raise NotFound("Person not found.")
    return user


async def is_guardian_or_self(uow: UnitOfWork, actor: Principal, student_id: UUID) -> bool:
    """True when the actor is the student, or a parent linked to them."""
    if actor.user_id == student_id:
        return True
    if SchoolRole.PARENT.value not in actor.roles:
        return False
    return await uow.guardians.is_guardian(actor.user_id, student_id)


class Guardians:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def _views(self, uow: UnitOfWork, links: list[GuardianLink]) -> list[GuardianView]:
        views = []
        for link in links:
            parent = await uow.users.get(link.parent_id)
            student = await uow.users.get(link.student_id)
            views.append(
                GuardianView(
                    parent_id=link.parent_id,
                    student_id=link.student_id,
                    parent_name=parent.full_name if parent else "",
                    student_name=student.full_name if student else "",
                    relationship=link.relationship.value,
                    created_at=link.created_at,
                )
            )
        return views

    async def link(
        self, actor: Principal, *, student_id: UUID, parent_id: UUID, relationship: str
    ) -> GuardianView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("students:update")
        kind = parse_relationship(relationship)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            student = await _school_user(uow, tenant_id, student_id)
            parent = await _school_user(uow, tenant_id, parent_id)
            existing = await uow.guardians.guardians_of(student_id)
            if any(g.parent_id == parent_id for g in existing):
                raise Conflict("This parent is already linked to this student.")
            link = GuardianLink.create(
                parent=parent,
                student=student,
                relationship=kind,
                existing_count=len(existing),
                by=actor.user_id,
                now=self._clock.now(),
            )
            if not await uow.guardians.add(link):
                raise Conflict("This parent is already linked to this student.")
            uow.audit(
                AuditEntry(
                    action="identity.guardian_linked",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="school_user",
                    target_id=str(student_id),
                    metadata={"parent_id": str(parent_id), "relationship": kind.value},
                )
            )
            await uow.commit()
        return GuardianView(
            parent_id=parent_id,
            student_id=student_id,
            parent_name=parent.full_name,
            student_name=student.full_name,
            relationship=kind.value,
            created_at=link.created_at,
        )

    async def unlink(self, actor: Principal, *, student_id: UUID, parent_id: UUID) -> None:
        actor.require_realm(Realm.SCHOOL)
        actor.require("students:update")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            if not await uow.guardians.remove(parent_id, student_id):
                raise NotFound("That parent is not linked to this student.")
            uow.audit(
                AuditEntry(
                    action="identity.guardian_unlinked",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="school_user",
                    target_id=str(student_id),
                    metadata={"parent_id": str(parent_id)},
                )
            )
            await uow.commit()

    async def guardians_of(self, actor: Principal, student_id: UUID) -> list[GuardianView]:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            if not actor.permissions.allows("students:read") and not await is_guardian_or_self(
                uow, actor, student_id
            ):
                raise PermissionDenied("You can only see your own family.")
            await _school_user(uow, tenant_id, student_id)
            return await self._views(uow, await uow.guardians.guardians_of(student_id))

    async def my_children(self, actor: Principal) -> list[GuardianView]:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            return await self._views(uow, await uow.guardians.children_of(actor.user_id))
