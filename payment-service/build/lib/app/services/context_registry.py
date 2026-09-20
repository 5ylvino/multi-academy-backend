from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentContextDef:
    key: str
    label: str
    description: str
    required_features: tuple[str, ...]
    allowed_gateways: tuple[str, ...]
    default_enabled: bool = True


PAYMENT_CONTEXTS: dict[str, PaymentContextDef] = {
    "school_invoice": PaymentContextDef(
        key="school_invoice",
        label="Pay invoice",
        description="Parent portal — Pay button on an outstanding invoice",
        required_features=("fees.gateway",),
        allowed_gateways=("paystack", "flutterwave", "opay", "palmpay", "momo", "monnify"),
    ),
    "school_installment": PaymentContextDef(
        key="school_installment",
        label="Pay installment",
        description="Parent portal — Pay button on a scheduled installment",
        required_features=("fees.gateway", "fees.installments"),
        allowed_gateways=("paystack", "flutterwave", "opay", "palmpay", "momo", "monnify"),
    ),
    "school_advance": PaymentContextDef(
        key="school_advance",
        label="Prepay fees",
        description="Parent portal — ahead-of-time school fee prepayment",
        required_features=("fees.gateway", "fees.advance_payment"),
        allowed_gateways=("paystack", "flutterwave", "opay", "palmpay", "momo", "monnify"),
    ),
    "tutoring_session": PaymentContextDef(
        key="tutoring_session",
        label="Pay tutoring session",
        description="Tutoring marketplace — Pay button on a booking",
        required_features=("tutoring.payments",),
        allowed_gateways=("paystack", "flutterwave", "opay", "palmpay", "momo"),
    ),
    "saas_subscription": PaymentContextDef(
        key="saas_subscription",
        label="Platform subscription",
        description="School onboarding — SaaS plan checkout",
        required_features=(),
        allowed_gateways=("paystack", "flutterwave"),
    ),
}


def get_context(key: str) -> PaymentContextDef | None:
    return PAYMENT_CONTEXTS.get(key)


def list_contexts() -> list[PaymentContextDef]:
    return list(PAYMENT_CONTEXTS.values())
