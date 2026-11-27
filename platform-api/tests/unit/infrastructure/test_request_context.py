"""Request details used to stamp audit rows."""

from __future__ import annotations

from app.infrastructure.observability import request_context
from app.infrastructure.observability.request_context import RequestContext


def test_client_ip_trusts_only_the_configured_hops() -> None:
    forwarded = "6.6.6.6, 198.51.100.4, 10.0.0.2"
    assert request_context.client_ip(forwarded, "10.0.0.9", trusted_proxy_hops=1) == "10.0.0.2"
    assert request_context.client_ip(forwarded, "10.0.0.9", trusted_proxy_hops=2) == (
        "198.51.100.4"
    )
    assert request_context.client_ip(forwarded, "10.0.0.9", trusted_proxy_hops=0) == "10.0.0.9"
    assert request_context.client_ip("1.2.3.4", "10.0.0.9", trusted_proxy_hops=3) == "10.0.0.9"
    assert request_context.client_ip(None, None, trusted_proxy_hops=1) is None


def test_user_agent_is_trimmed_and_printable() -> None:
    assert request_context.clean_user_agent("a\nb\x00c") == "abc"
    assert len(request_context.clean_user_agent("x" * 1000) or "") == 256
    assert request_context.clean_user_agent("") is None


def test_context_is_scoped_to_the_block() -> None:
    assert request_context.current() == RequestContext()
    with request_context.bound(RequestContext(request_id="r1")):
        assert request_context.current().request_id == "r1"
    assert request_context.current().request_id is None
