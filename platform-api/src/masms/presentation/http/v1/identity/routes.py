"""Sign-in, refresh, sign-out and current user, for both realms."""

from __future__ import annotations

from fastapi import APIRouter, status

from masms.domain.identity.roles import Realm
from masms.presentation.http.dependencies.auth import PlatformPrincipal, SchoolPrincipal
from masms.presentation.http.dependencies.context import ClientInfoDep, UseCasesDep
from masms.presentation.http.v1.identity.mfa_schemas import MfaChallengeResponse
from masms.presentation.http.v1.identity.schemas import (
    CurrentUserResponse,
    PlatformSignInRequest,
    RefreshRequest,
    SchoolSignInRequest,
    SessionResponse,
)
from masms.use_cases.identity.mfa import MfaChallenge
from masms.use_cases.identity.refresh_session import RefreshCommand
from masms.use_cases.identity.sign_in import SignInCommand
from masms.use_cases.identity.sign_out import SignOutCommand

school_router = APIRouter(prefix="/auth", tags=["school auth"])
platform_router = APIRouter(prefix="/platform/auth", tags=["platform auth"])


@school_router.post("/sign-in", response_model=SessionResponse)
async def school_sign_in(
    body: SchoolSignInRequest, uc: UseCasesDep, client: ClientInfoDep
) -> SessionResponse:
    issued = await uc.school_sign_in(
        SignInCommand(
            realm=Realm.SCHOOL,
            email=body.email,
            password=body.password,
            school_slug=body.school,
            client=client,
        )
    )
    if isinstance(issued, MfaChallenge):  # school sign-in has no second step yet
        raise RuntimeError("unexpected MFA challenge for a school sign-in")
    return SessionResponse.of(issued)


@school_router.post("/refresh", response_model=SessionResponse)
async def school_refresh(
    body: RefreshRequest, uc: UseCasesDep, client: ClientInfoDep
) -> SessionResponse:
    issued = await uc.school_refresh(
        RefreshCommand(realm=Realm.SCHOOL, refresh_token=body.refresh_token, client=client)
    )
    return SessionResponse.of(issued)


@school_router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def school_sign_out(body: RefreshRequest, uc: UseCasesDep) -> None:
    await uc.sign_out(SignOutCommand(refresh_token=body.refresh_token))


@school_router.get("/me", response_model=CurrentUserResponse)
async def school_me(principal: SchoolPrincipal, uc: UseCasesDep) -> CurrentUserResponse:
    return CurrentUserResponse.model_validate(await uc.current_user(principal))


@platform_router.post("/sign-in", response_model=MfaChallengeResponse | SessionResponse)
async def platform_sign_in(
    body: PlatformSignInRequest, uc: UseCasesDep, client: ClientInfoDep
) -> MfaChallengeResponse | SessionResponse:
    """A correct password returns an MFA challenge; finish at ``/platform/auth/mfa/*``."""
    issued = await uc.platform_sign_in(
        SignInCommand(
            realm=Realm.PLATFORM,
            email=body.email,
            password=body.password,
            school_slug=None,
            client=client,
        )
    )
    if isinstance(issued, MfaChallenge):
        return MfaChallengeResponse.of(issued)
    return SessionResponse.of(issued)


@platform_router.post("/refresh", response_model=SessionResponse)
async def platform_refresh(
    body: RefreshRequest, uc: UseCasesDep, client: ClientInfoDep
) -> SessionResponse:
    issued = await uc.platform_refresh(
        RefreshCommand(realm=Realm.PLATFORM, refresh_token=body.refresh_token, client=client)
    )
    return SessionResponse.of(issued)


@platform_router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def platform_sign_out(body: RefreshRequest, uc: UseCasesDep) -> None:
    await uc.sign_out(SignOutCommand(refresh_token=body.refresh_token))


@platform_router.get("/me", response_model=CurrentUserResponse)
async def platform_me(principal: PlatformPrincipal, uc: UseCasesDep) -> CurrentUserResponse:
    return CurrentUserResponse.model_validate(await uc.current_user(principal))
