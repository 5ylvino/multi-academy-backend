"""Outbox delivery semantics: no fabricated success, bounded retries."""

from __future__ import annotations

import json

import pytest

from app.models import OutboxEvent
from app.services import outbox, outbox_worker


@pytest.fixture(autouse=True)
def _reset_warning_flag():
    outbox._warned_no_delivery = False
    yield


class TestAutoPublishDefault:
    def test_production_without_webhook_does_not_fabricate_delivery(
        self, session, monkeypatch
    ):
        """The critical inversion: in production without REDIS_URL the old code
        stamped every event delivered without sending it, so schools silently
        kept serving stale flags and provider credentials forever."""
        cfg = outbox.get_settings()
        monkeypatch.setattr(cfg, "environment", "production", raising=False)
        monkeypatch.setattr(cfg, "nest_config_webhook_url", "", raising=False)

        event = outbox.enqueue_outbox(
            session, event_type="config.invalidate", aggregate_type="tenant"
        )
        session.commit()

        assert event.published is False
        assert event.delivery_status == "pending"

    def test_development_without_webhook_may_stub(self, session, monkeypatch):
        cfg = outbox.get_settings()
        monkeypatch.setattr(cfg, "environment", "development", raising=False)
        monkeypatch.setattr(cfg, "nest_config_webhook_url", "", raising=False)

        event = outbox.enqueue_outbox(session, event_type="config.invalidate")
        session.commit()
        assert event.published is True
        assert event.delivery_status == "delivered"

    def test_configured_webhook_always_queues_for_real_delivery(
        self, session, monkeypatch
    ):
        cfg = outbox.get_settings()
        monkeypatch.setattr(cfg, "environment", "development", raising=False)
        monkeypatch.setattr(
            cfg, "nest_config_webhook_url", "https://school.example/hook", raising=False
        )

        event = outbox.enqueue_outbox(session, event_type="config.invalidate")
        session.commit()
        assert event.published is False

    def test_payload_is_stored_as_json(self, session, monkeypatch):
        cfg = outbox.get_settings()
        monkeypatch.setattr(cfg, "environment", "development", raising=False)
        event = outbox.enqueue_outbox(
            session, event_type="x", payload={"tenantId": 4, "reason": "test"}
        )
        session.commit()
        assert json.loads(event.payload)["tenantId"] == 4


class TestRetryBackoff:
    def test_backoff_grows_and_is_capped(self):
        first = outbox_worker._backoff_seconds(1)
        second = outbox_worker._backoff_seconds(2)
        assert second > first
        assert (
            outbox_worker._backoff_seconds(50) == outbox_worker.BACKOFF_CAP_SECONDS
        ), "backoff must not grow without bound"

    def test_repeated_failures_dead_letter(self, session):
        event = OutboxEvent(event_type="config.invalidate", payload="{}", delivery_log="[]")
        session.add(event)
        session.commit()

        for _ in range(outbox_worker.MAX_ATTEMPTS):
            outbox_worker._mark_failed(event, "HTTP 500: boom")

        assert event.delivery_status == "dead_letter"
        assert event.next_attempt_at is None
        assert event.published is False

    def test_delivery_log_is_bounded(self, session):
        event = OutboxEvent(event_type="x", payload="{}", delivery_log="[]")
        for i in range(40):
            outbox_worker._append_delivery_log(
                event, ok=False, channel="webhook", message=f"attempt {i}"
            )
        assert len(json.loads(event.delivery_log)) == 20

    def test_corrupt_delivery_log_does_not_crash(self, session):
        event = OutboxEvent(event_type="x", payload="{}", delivery_log="not json")
        outbox_worker._append_delivery_log(event, ok=True, channel="webhook", message="ok")
        assert len(json.loads(event.delivery_log)) == 1

    def test_mark_delivered_clears_retry_state(self, session):
        event = OutboxEvent(event_type="x", payload="{}", delivery_log="[]")
        outbox_worker._mark_failed(event, "transient")
        outbox_worker._mark_delivered(event, channel="webhook", message="HTTP 200")
        assert event.published is True
        assert event.delivery_status == "delivered"
        assert event.next_attempt_at is None
        assert event.last_error == ""


class TestBatchWithoutWebhook:
    def test_pending_rows_are_left_pending_not_marked_sent(self, session, monkeypatch):
        monkeypatch.setattr(outbox_worker.settings, "nest_config_webhook_url", "", raising=False)
        session.add(OutboxEvent(event_type="config.invalidate", payload="{}", delivery_log="[]"))
        session.commit()

        result = outbox_worker.process_outbox_batch(session)
        assert result["published"] == 0
        assert result["webhookConfigured"] is False
        assert session.query(OutboxEvent).filter_by(published=False).count() == 1


class TestWebhookSignature:
    def test_signature_covers_timestamp_and_body(self):
        body = b'{"eventType":"config.invalidate"}'
        a = outbox_worker._sign(body, "secret", "1700000000")
        b = outbox_worker._sign(body, "secret", "1700000001")
        assert a != b, "a replayed signature must not stay valid at a new timestamp"

    def test_signature_depends_on_body(self):
        a = outbox_worker._sign(b'{"a":1}', "secret", "1700000000")
        b = outbox_worker._sign(b'{"a":2}', "secret", "1700000000")
        assert a != b

    def test_signature_depends_on_secret(self):
        body = b"{}"
        assert outbox_worker._sign(body, "s1", "1") != outbox_worker._sign(body, "s2", "1")
