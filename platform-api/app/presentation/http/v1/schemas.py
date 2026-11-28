"""Base schema shared by every v1 request and response model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)


class PageMeta(ApiModel):
    next_cursor: str | None
