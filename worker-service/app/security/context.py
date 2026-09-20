from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    actor_id: str
