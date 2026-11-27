"""Exchange a refresh token for a new access token, rotating the refresh token."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.identity.events import RefreshTokenReuseDetected
from app.domain.identity.roles import Realm
from app.domain.identity.user import UserStatus
from app.domain.shared.errors import AuthenticationFailed
from app.use_cases.identity.session_issuer import ClientInfo, IssuedSession, SessionIssuer
from app.use_cases.shared.ports import Clock, Scope, SecretHasher, UnitOfWorkFactory

_EXPIRED = "Your session has ended. Please sign in again."


@dataclass(frozen=True, slots=True)
class RefreshCommand:
    realm: Realm
    refresh_token: str
    client: ClientInfo


class RefreshSessionUseCase:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        secret_hasher: SecretHasher,
        issuer: SessionIssuer,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_hasher = secret_hasher
        self._issuer = issuer
        self._clock = clock

    async def __call__(self, cmd: RefreshCommand) -> IssuedSession:
        now = self._clock.now()
        token_hash = self._secret_hasher.hash(cmd.refresh_token)
        scope = Scope.platform_wide() if cmd.realm is Realm.PLATFORM else Scope.registry()
        async with self._uow_factory(scope) as uow:
            current = await uow.sessions.get_by_token_hash(token_hash)
            if current is None or current.realm is not cmd.realm:
                raise AuthenticationFailed(_EXPIRED)

            if current.was_rotated:
                # A rotated token came back, so someone else holds a copy.
                # End every session in the family.
                await uow.sessions.revoke_family(current.family_id, now)
                uow.publish(
                    RefreshTokenReuseDetected(
                        tenant_id=current.tenant_id,
                        user_id=current.user_id,
                        family_id=current.family_id,
                    )
                )
                await uow.commit()
                raise AuthenticationFailed(_EXPIRED)

            if not current.is_active(now):
                raise AuthenticationFailed(_EXPIRED)

            config_version: int | None = None
            if current.tenant_id is not None:
                tenant = await uow.tenants.get(current.tenant_id)
                if tenant is None or not tenant.is_serving:
                    raise AuthenticationFailed(_EXPIRED)
                config_version = tenant.config_version
                await uow.enter_tenant(tenant.id)

            user = await uow.users.get(current.user_id)
            if user is None or user.status is not UserStatus.ACTIVE:
                await uow.sessions.revoke_family(current.family_id, now)
                await uow.commit()
                raise AuthenticationFailed(_EXPIRED)

            issued, successor = await self._issuer.issue(
                uow,
                user,
                client=cmd.client,
                tenant_config_version=config_version,
                family_id=current.family_id,
            )
            current.rotate_to(successor, now)
            await uow.sessions.save(current)
            await uow.commit()
            return issued
