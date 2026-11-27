"""Platform request and response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from masms.presentation.http.v1.schemas import ApiModel


class FlagResponse(ApiModel):
    key: str
    name: str
    description: str
    default_enabled: bool
    kill_switch: bool
    rollout_percent: int
    updated_at: datetime


class UpsertFlagRequest(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    default_enabled: bool = False
    kill_switch: bool = False
    rollout_percent: int = Field(default=0, ge=0, le=100)


class FlagOverrideRequest(ApiModel):
    enabled: bool | None = Field(description="true or false sets the override; null removes it.")


class PlanResponse(ApiModel):
    code: str
    name: str
    features: list[str]
    student_limit: int | None
    active: bool


class UpsertPlanRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    features: list[str] = Field(default_factory=list, max_length=500)
    student_limit: int | None = Field(default=None, ge=1)
    active: bool = True
