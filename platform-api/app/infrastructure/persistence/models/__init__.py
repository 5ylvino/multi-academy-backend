"""ORM tables, one module per feature. Importing this package registers every table on ``Base``."""

from app.infrastructure.persistence.models import (
    academics,
    attendance,
    communication,
    finance,
    hostel,
    identity,
    inventory,
    operations,
    payroll,
    platform,
    safeguarding,
    subscriptions,
    system,
    tenancy,
    transport,
)

__all__ = [
    "academics",
    "attendance",
    "communication",
    "finance",
    "hostel",
    "identity",
    "inventory",
    "operations",
    "payroll",
    "platform",
    "safeguarding",
    "subscriptions",
    "system",
    "tenancy",
    "transport",
]
