"""ORM tables, one module per feature. Importing this package registers every table on ``Base``."""

from masms.infrastructure.persistence.models import (
    academics,
    attendance,
    finance,
    identity,
    platform,
    system,
    tenancy,
)

__all__ = ["academics", "attendance", "finance", "identity", "platform", "system", "tenancy"]
