ADMIN_ROLES = frozenset({"director", "school_admin", "bursar"})


def is_admin_role(roles: list[str]) -> bool:
    return any(r in ADMIN_ROLES for r in roles)
