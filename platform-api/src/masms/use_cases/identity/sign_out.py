"""Sign out: revoke the whole session family of a refresh token."""

from __future__ import annotations

from dataclasses import dataclass

from masms.use_cases.shared.ports import Clock, Scope, SecretHasher, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class SignOutCommand:
    refresh_token: str


class SignOut:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, secret_hasher: SecretHasher, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_hasher = secret_hasher
        self._clock = clock

    async def __call__(self, cmd: SignOutCommand) -> None:
        async with self._uow_factory(Scope.registry()) as uow:
            session = await uow.sessions.get_by_token_hash(
                self._secret_hasher.hash(cmd.refresh_token)
            )
            if session is None:
                return  # Signing out twice is not an error.
            await uow.sessions.revoke_family(session.family_id, self._clock.now())
            await uow.commit()
