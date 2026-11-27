"""ORM tables, one module per feature. Importing this package registers every table on ``Base``."""

from app.infrastructure.persistence.models import (
    academics,
    attendance,
    communication,
    finance,
    identity,
    inventory,
    payroll,
    platform,
    subscriptions,
    system,
    tenancy,
)

__all__ = [
    "academics",
    "attendance",
    "communication",
    "finance",
    "identity",
    "inventory",
    "payroll",
    "platform",
    "subscriptions",
    "system",
    "tenancy",
]
