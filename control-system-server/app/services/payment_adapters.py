"""Provider-neutral payment execution and reconciliation ports.

Concrete gateways implement these protocols in deployment-specific adapters.
The control plane deliberately ships no KIRA adapter.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PaymentRequest:
    idempotency_key: str
    amount_minor: int
    currency: str
    provider_id: str
    metadata: dict


@dataclass(frozen=True)
class PaymentResult:
    accepted: bool
    provider_reference: str = ""
    status: str = "pending"
    message: str = ""


class PaymentExecutor(Protocol):
    def collect(self, request: PaymentRequest) -> PaymentResult: ...


class PaymentReconciler(Protocol):
    def reconcile(self, provider_reference: str) -> PaymentResult: ...


def validate_provider_adapter(provider_id: str) -> None:
    if provider_id.strip().lower() == "kira":
        raise ValueError("KIRA is excluded from the provider adapter contract")


def build_split_amounts(amount_minor: int, allocations: list[dict]) -> list[dict]:
    """Convert validated basis-point rules into deterministic minor amounts."""
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")
    if sum(int(row.get("percentage_bps", 0)) for row in allocations) != 10000:
        raise ValueError("allocations must total 10000 basis points")
    result = []
    assigned = 0
    for index, row in enumerate(allocations):
        if index == len(allocations) - 1:
            share = amount_minor - assigned
        else:
            share = amount_minor * int(row["percentage_bps"]) // 10000
            assigned += share
        result.append({**row, "amount_minor": share})
    return result
