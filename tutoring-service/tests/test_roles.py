from app.security.roles import is_teacher_role


def test_teacher_roles() -> None:
    assert is_teacher_role(["subject_teacher"])
    assert is_teacher_role(["class_teacher", "parent"])
    assert not is_teacher_role(["parent", "student"])
