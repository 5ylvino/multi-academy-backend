"""DNS and certificate provisioning port for tenant domains."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DomainProvisioningResult:
    hostname: str
    status: str
    ssl_status: str
    verification_token: str = ""
    message: str = ""


class DomainProvisioner(Protocol):
    def provision(self, hostname: str, *, kind: str) -> DomainProvisioningResult: ...

    def remove(self, hostname: str) -> DomainProvisioningResult: ...


def validate_hostname_kind(hostname: str, kind: str) -> None:
    if kind not in ("subdomain", "custom"):
        raise ValueError("kind must be subdomain or custom")
    if not hostname or "." not in hostname:
        raise ValueError("hostname must be a fully qualified DNS name")
