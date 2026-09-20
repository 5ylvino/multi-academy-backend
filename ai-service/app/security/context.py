from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    provider_id: str | None = None
    model: str | None = None
    max_tokens: int | None = None


@dataclass
class RequestContext:
    tenant_id: str
    actor_id: str
    roles: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    request_id: str | None = None
    provider: ProviderConfig | None = None
