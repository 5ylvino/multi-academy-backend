"""Delivers outbox events to their handlers, at least once, with backoff and a dead-letter state.

Safe to run in many worker processes at once: rows are claimed with ``FOR UPDATE SKIP LOCKED``.
Handlers must be idempotent, because an event can be delivered again after a crash.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.persistence.models.system import OutboxEventRow

Handler = Callable[[Mapping[str, Any]], Awaitable[None]]

_log = structlog.get_logger(__name__)
MAX_ATTEMPTS = 10
_DEAD = datetime(9999, 1, 1, tzinfo=UTC)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 3600))


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: Mapping[str, list[Handler]],
        *,
        batch_size: int = 100,
    ) -> None:
        self._factory = session_factory
        self._handlers = handlers
        self._batch = batch_size

    async def run_once(self) -> int:
        """Process one batch. Returns how many events were handled (successfully or not)."""
        async with self._factory() as session, session.begin():
            await session.execute(select(func.set_config("app.rls_bypass", "on", True)))
            rows = list(
                (
                    await session.scalars(
                        select(OutboxEventRow)
                        .where(
                            OutboxEventRow.dispatched_at.is_(None),
                            OutboxEventRow.available_at <= func.now(),
                        )
                        .order_by(OutboxEventRow.available_at)
                        .limit(self._batch)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                await self._deliver(row)
            return len(rows)

    async def _deliver(self, row: OutboxEventRow) -> None:
        now = datetime.now(UTC)
        try:
            for handler in self._handlers.get(row.name, []):
                await handler(row.payload)
        except Exception as exc:
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if row.attempts >= MAX_ATTEMPTS:
                row.available_at = _DEAD
                _log.error(
                    "outbox.dead_lettered",
                    event_name=row.name,
                    event_id=str(row.id),
                    error=row.last_error,
                )
            else:
                row.available_at = now + _backoff(row.attempts)
                _log.warning(
                    "outbox.retry", event_name=row.name, event_id=str(row.id), attempts=row.attempts
                )
            return
        row.dispatched_at = now
        if row.sensitive_fields:
            row.payload = {
                k: ("[redacted]" if k in row.sensitive_fields else v)
                for k, v in row.payload.items()
            }
        _log.info("outbox.dispatched", event_name=row.name, event_id=str(row.id))

    async def purge_dispatched(self, older_than: timedelta = timedelta(days=7)) -> int:
        async with self._factory() as session, session.begin():
            result = await session.execute(
                text("DELETE FROM outbox_events WHERE dispatched_at < :cutoff"),
                {"cutoff": datetime.now(UTC) - older_than},
            )
            return int(result.rowcount)  # type: ignore[attr-defined]
