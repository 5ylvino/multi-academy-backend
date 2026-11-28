"""Owner-console two-step verification: finish sign-in, enrol, recovery codes, admin reset."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.presentation.http.dependencies.auth import PlatformPrincipal
from app.presentation.http.dependencies.context import ClientInfoDep, UseCasesDep
from app.presentation.http.v1.identity.mfa_schemas import (
    MfaEnrolledResponse,
    MfaEnrolRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    RecoveryCodesResponse,
    RegenerateCodesRequest,
)
from app.presentation.http.v1.identity.schemas import SessionResponse
from app.use_cases.identity.mfa import ConfirmMfaCommand, VerifyMfaCommand

router = APIRouter(prefix="/platform", tags=["platform auth"])


@router.post("/auth/mfa/verify", response_model=SessionResponse)
async def verify(body: MfaVerifyRequest, uc: UseCasesDep, client: ClientInfoDep) -> SessionResponse:
    issued = await uc.verify_mfa(
        VerifyMfaCommand(challenge_token=body.challenge_token, code=body.code, client=client)
    )
    return SessionResponse.of(issued)


@router.post("/auth/mfa/enrolment", response_model=MfaSetupResponse)
async def start_enrolment(body: MfaEnrolRequest, uc: UseCasesDep) -> MfaSetupResponse:
    return MfaSetupResponse.model_validate(await uc.start_mfa_enrolment(body.challenge_token))


@router.post("/auth/mfa/enrolment/confirm", response_model=MfaEnrolledResponse)
async def confirm_enrolment(
    body: MfaVerifyRequest, uc: UseCasesDep, client: ClientInfoDep
) -> MfaEnrolledResponse:
    result = await uc.confirm_mfa_enrolment(
        ConfirmMfaCommand(challenge_token=body.challenge_token, code=body.code, client=client)
    )
    return MfaEnrolledResponse(
        session=SessionResponse.of(result.session), recovery_codes=result.recovery_codes
    )


@router.post("/auth/mfa/recovery-codes", response_model=RecoveryCodesResponse)
async def regenerate_codes(
    body: RegenerateCodesRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> RecoveryCodesResponse:
    codes = await uc.regenerate_recovery_codes(principal, body.code)
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/users/{user_id}/mfa/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_mfa(user_id: UUID, principal: PlatformPrincipal, uc: UseCasesDep) -> None:
    await uc.reset_platform_mfa(principal, user_id)
