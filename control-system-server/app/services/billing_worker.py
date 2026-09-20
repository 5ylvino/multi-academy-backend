"""Periodic platform jobs — invoice generation, dunning, and offboard wipes."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.database import SessionLocal
from app.services.billing import (
    generate_due_invoices,
    run_courtesy_unlock_expiry_pass,
    run_dunning_pass,
)
from app.services.offboarding import dispatch_due_wipes
from app.services.trials import run_trial_pass, run_unsubscribed_pass

logger = logging.getLogger("control.billing_worker")
settings = get_settings()


def _run_pass() -> dict:
    """One scheduling pass. Each stage owns its own session so a failure in one
    does not roll back the others."""
    summary: dict[str, int | str] = {}

    db = SessionLocal()
    try:
        trials = run_trial_pass(db)
        if any(trials.values()):
            db.commit()
        else:
            db.rollback()
        summary.update({f"trial{key.capitalize()}": value for key, value in trials.items()})
    except Exception:
        logger.exception("Trial pass failed")
        db.rollback()
        summary["trial"] = "error"
    finally:
        db.close()

    # generate_due_invoices commits per subscription internally.
    db = SessionLocal()
    try:
        invoices = generate_due_invoices(db, staff_email="system:billing_worker")
        summary["invoices"] = len(invoices)
    except Exception:
        logger.exception("Invoice generation failed")
        db.rollback()
        summary["invoices"] = "error"
    finally:
        db.close()

    db = SessionLocal()
    try:
        suspended = run_unsubscribed_pass(
            db, grace_days=max(1, int(getattr(settings, "dunning_grace_days", 7) or 7))
        )
        if suspended:
            db.commit()
        else:
            db.rollback()
        summary["unsubscribedSuspended"] = suspended
    except Exception:
        logger.exception("Unsubscribed tenant enforcement failed")
        db.rollback()
        summary["unsubscribedSuspended"] = "error"
    finally:
        db.close()

    db = SessionLocal()
    try:
        courtesy_expired = run_courtesy_unlock_expiry_pass(db)
        if courtesy_expired:
            db.commit()
        else:
            db.rollback()
        summary["courtesyUnlockExpired"] = courtesy_expired
    except Exception:
        logger.exception("Courtesy unlock expiry pass failed")
        db.rollback()
        summary["courtesyUnlockExpired"] = "error"
    finally:
        db.close()

    db = SessionLocal()
    try:
        grace_days = max(1, int(getattr(settings, "dunning_grace_days", 7) or 7))
        dunning = run_dunning_pass(db, grace_days=grace_days)
        if dunning:
            db.commit()
        else:
            db.rollback()
        summary["dunning"] = len(dunning)
    except Exception:
        logger.exception("Dunning pass failed")
        db.rollback()
        summary["dunning"] = "error"
    finally:
        db.close()

    db = SessionLocal()
    try:
        wipes = dispatch_due_wipes(db)
        if wipes:
            db.commit()
        else:
            db.rollback()
        summary["wipesDispatched"] = len(wipes)
    except Exception:
        logger.exception("Offboard wipe dispatch failed")
        db.rollback()
        summary["wipesDispatched"] = "error"
    finally:
        db.close()

    return summary


async def billing_worker_loop(stop_event: asyncio.Event) -> None:
    interval = max(60, getattr(settings, "billing_worker_interval_seconds", 300))
    logger.info("Billing worker started (interval=%ss)", interval)
    while not stop_event.is_set():
        try:
            # Synchronous DB work stays off the event loop so it cannot stall
            # request handling in the same process.
            summary = await asyncio.to_thread(_run_pass)
            if any(v for v in summary.values()):
                logger.info("Billing pass: %s", summary)
        except Exception:
            logger.exception("Billing worker pass failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("Billing worker stopped")
