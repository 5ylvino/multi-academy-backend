"""Outbox worker: ``python -m app.presentation.workers.outbox``.

Run as many replicas as needed; each claims different rows. Stops cleanly on SIGTERM.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from app.infrastructure.config.settings import get_settings
from app.infrastructure.container import build_container
from app.infrastructure.observability.logging import configure_logging

_log = structlog.get_logger(__name__)
IDLE_SLEEP_SECONDS = 0.5
PURGE_EVERY_SECONDS = 3600


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    container = build_container(settings, role="worker")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    _log.info("outbox_worker.started")
    last_purge = loop.time()
    try:
        while not stop.is_set():
            try:
                handled = await container.outbox.run_once()
            except Exception:
                _log.exception("outbox_worker.batch_failed")
                handled = 0
            if loop.time() - last_purge > PURGE_EVERY_SECONDS:
                purged = await container.outbox.purge_dispatched()
                _log.info("outbox_worker.purged", rows=purged)
                last_purge = loop.time()
            if handled == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
    finally:
        await container.aclose()
        _log.info("outbox_worker.stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
