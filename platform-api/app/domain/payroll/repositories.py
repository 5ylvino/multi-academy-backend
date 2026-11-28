"""Payroll persistence, scoped to the current school."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.payroll.pay import PayProfile, PayrollSettings, PayRun, Payslip, RunTotals
from app.domain.shared.value_objects import Page


@dataclass(frozen=True, slots=True)
class RunListing:
    """A pay run without its payslips, with the totals stored when it was last calculated."""

    run: PayRun
    totals: RunTotals


@dataclass(frozen=True, slots=True)
class StaffPayslip:
    """One payslip with the run it belongs to, for a staff member's own history."""

    run: PayRun
    payslip: Payslip


class PayrollRepository(Protocol):
    async def get_settings(self) -> PayrollSettings | None: ...

    async def save_settings(
        self, settings: PayrollSettings, *, by: UUID, now: datetime
    ) -> None: ...

    async def get_profile(
        self, staff_id: UUID, *, for_update: bool = False
    ) -> PayProfile | None: ...

    async def save_profile(self, profile: PayProfile) -> None:
        """Insert or replace the profile."""
        ...

    async def list_profiles(
        self, *, include_inactive: bool, cursor: str | None, limit: int
    ) -> Page[PayProfile]: ...

    async def active_profiles(self) -> list[PayProfile]: ...

    async def add_run(self, run: PayRun) -> None: ...

    async def get_run(self, run_id: UUID, *, for_update: bool = False) -> PayRun | None: ...

    async def save_run(self, run: PayRun, *, replace_payslips: bool = False) -> None: ...

    async def open_run_for(self, year: int, month: int) -> UUID | None:
        """The run for that month that isn't void, if any."""
        ...

    async def list_runs(
        self, *, year: int | None, cursor: str | None, limit: int
    ) -> Page[RunListing]: ...

    async def payslips_of(
        self, staff_id: UUID, *, cursor: str | None, limit: int
    ) -> Page[StaffPayslip]:
        """Approved and paid payslips of one person, newest month first."""
        ...
