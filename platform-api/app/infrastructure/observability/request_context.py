"""Who is calling, from where: bound once per request and read by the audit writer.

A ``ContextVar`` follows the request through ``await`` and into thread-pool dependencies, so the
unit of work can stamp audit rows with the request id, client IP and device without every use case
passing them along. Outside a request (CLI, workers) the values are empty.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

_MAX_USER_AGENT = 256


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


_EMPTY = RequestContext()
_current: ContextVar[RequestContext] = ContextVar("masms_request_context", default=_EMPTY)


def current() -> RequestContext:
    return _current.get()


def bind(context: RequestContext) -> Token[RequestContext]:
    return _current.set(context)


def reset(token: Token[RequestContext]) -> None:
    _current.reset(token)


@contextmanager
def bound(context: RequestContext) -> Iterator[RequestContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def client_ip(
    forwarded_for: str | None, peer: str | None, *, trusted_proxy_hops: int
) -> str | None:
    """Client IP, trusting only the configured number of proxy hops in X-Forwarded-For.

    Each trusted proxy appends the address it received the request from, so the client is the
    entry ``trusted_proxy_hops`` from the right. Anything further left was sent by the client and
    can be forged.
    """
    if forwarded_for and trusted_proxy_hops > 0:
        chain = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if len(chain) >= trusted_proxy_hops:
            return chain[-trusted_proxy_hops][:45]
    return peer[:45] if peer else None


def clean_user_agent(value: str | None) -> str | None:
    if not value:
        return None
    printable = "".join(ch for ch in value if ch.isprintable())
    return printable[:_MAX_USER_AGENT] or None
