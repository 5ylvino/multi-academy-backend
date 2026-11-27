"""Two-step verification models for owner-console sign-in."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from masms.presentation.http.v1.identity.schemas import SessionResponse
from masms.presentation.http.v1.schemas import ApiModel
from masms.use_cases.identity.mfa import MfaChallenge

Code = Field(min_length=6, max_length=16, description="6-digit app code or a recovery code")


class MfaChallengeResponse(ApiModel):
    """Returned by sign-in instead of a session when a second step is needed."""

    next_step: Literal["verify", "enrol"]
    challenge_token: str
    expires_in: int

    @classmethod
    def of(cls, challenge: MfaChallenge) -> MfaChallengeResponse:
        return cls(
            next_step="verify" if challenge.purpose.value == "verify" else "enrol",
            challenge_token=challenge.token,
            expires_in=challenge.expires_in,
        )


class MfaVerifyRequest(ApiModel):
    challenge_token: str = Field(min_length=16, max_length=128)
    code: str = Code


class MfaEnrolRequest(ApiModel):
    challenge_token: str = Field(min_length=16, max_length=128)


class MfaSetupResponse(ApiModel):
    secret: str
    otpauth_uri: str


class MfaEnrolledResponse(ApiModel):
    session: SessionResponse
    recovery_codes: list[str]


class RegenerateCodesRequest(ApiModel):
    code: str = Field(pattern=r"^\d{6}$")


class RecoveryCodesResponse(ApiModel):
    recovery_codes: list[str]
