TEACHER_ROLES = frozenset(
    {
        "teacher",
        "subject_teacher",
        "class_teacher",
        "head_teacher",
        "principal",
        "assistant_head_teacher",
    }
)


def is_teacher_role(roles: list[str]) -> bool:
    return bool(TEACHER_ROLES.intersection(r.lower() for r in roles))
