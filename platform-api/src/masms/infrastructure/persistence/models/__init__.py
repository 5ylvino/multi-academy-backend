"""ORM tables, one module per feature. Importing this package registers every table on ``Base``."""

from masms.infrastructure.persistence.models import identity, platform, system, tenancy

__all__ = ["identity", "platform", "system", "tenancy"]
