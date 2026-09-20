from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    actor_id: str
    roles: list[str]
    features: list[str]
    request_id: str | None = None
