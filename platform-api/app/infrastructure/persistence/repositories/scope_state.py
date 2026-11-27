"""Mutable per-transaction scope shared by the unit of work and its repositories."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass
class ScopeState:
    tenant_id: UUID | None = None
    platform: bool = False
