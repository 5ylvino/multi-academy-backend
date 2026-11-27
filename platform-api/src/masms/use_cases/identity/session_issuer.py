"""Issues an access token and a rotating refresh token for a signed-in user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from masms.domain.identity.session import RefreshSession
from masms.domain.identity.user import UserAccount
from masms.use_cases.shared.ports import (
    AccessClaims,
    Clock,
    SecretGenerator,
    SecretHasher,
    TokenService,
    UnitOfWork,
)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user_id: UUID
    tenant_id: UUID | None
    realm: str
    roles: list[str]


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip_address: str | None = None
    user_agent: str | None = None


class SessionIssuer:
    def __init__(
        self,
        *,
        tokens: TokenService,
        secrets: SecretGenerator,
        secret_hasher: SecretHasher,
        clock: Clock,
        refresh_ttl: timedelta,
    ) -> None:
        self._tokens = tokens
        self._secrets = secrets
        self._secret_hasher = secret_hasher
        self._clock = clock
        self._refresh_ttl = refresh_ttl

    async def issue(
        self,
        uow: UnitOfWork,
        user: UserAccount,
        *,
        client: ClientInfo,
        tenant_config_version: int | None,
        family_id: UUID | None = None,
    ) -> tuple[IssuedSession, RefreshSession]:
        now = self._clock.now()
        refresh_token = self._secrets.token()
        session = RefreshSession.start(
            user_id=user.id,
            realm=user.realm,
            tenant_id=user.tenant_id,
            token_hash=self._secret_hasher.hash(refresh_token),
            now=now,
            ttl=self._refresh_ttl,
            family_id=family_id,
            user_agent=(client.user_agent or "")[:400] or None,
            ip_address=client.ip_address,
        )
        await uow.sessions.add(session)
        claims = AccessClaims(
            realm=user.realm,
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=user.roles,
            capabilities=user.capabilities,
            primary_owner=user.primary_owner,
            session_family=session.family_id,
            tenant_config_version=tenant_config_version,
            token_id=self._secrets.token()[:22],
            expires_at=now + timedelta(seconds=self._tokens.access_ttl_seconds),
        )
        issued = IssuedSession(
            access_token=self._tokens.issue_access(claims),
            access_expires_in=self._tokens.access_ttl_seconds,
            refresh_token=refresh_token,
            refresh_expires_at=session.expires_at,
            user_id=user.id,
            tenant_id=user.tenant_id,
            realm=user.realm.value,
            roles=sorted(user.roles),
        )
        return issued, session
