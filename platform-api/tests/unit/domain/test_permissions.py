from __future__ import annotations

import pytest

from masms.domain.identity.permissions import (
    PermissionSet,
    parse_school_roles,
    validate_custom_capabilities,
)
from masms.domain.identity.roles import PlatformRole, SchoolRole
from masms.domain.identity.school_catalog import ROLE_ONLY_CAPABILITIES, SCHOOL_ROLE_PERMISSIONS
from masms.domain.shared.errors import ValidationFailed


def test_every_school_role_has_a_catalog_entry() -> None:
    assert set(SCHOOL_ROLE_PERMISSIONS) == set(SchoolRole)


def test_bursar_processes_payments_but_cannot_approve_results() -> None:
    perms = PermissionSet.for_school([SchoolRole.BURSAR])
    assert perms.allows("payments:process")
    assert not perms.allows("results:approve")


def test_director_cannot_process_payments() -> None:
    assert not PermissionSet.for_school([SchoolRole.DIRECTOR]).allows("payments:process")


def test_manage_implies_every_action_on_the_resource() -> None:
    perms = PermissionSet(frozenset({"inventory:manage"}))
    assert perms.allows("inventory:delete")
    assert not perms.allows("budgets:read")


def test_primary_owner_has_wildcard() -> None:
    assert PermissionSet.for_school([SchoolRole.DIRECTOR], primary_owner=True).allows(
        "anything:at_all"
    )


def test_platform_auditor_is_read_only() -> None:
    perms = PermissionSet.for_platform([PlatformRole.AUDITOR])
    assert perms.allows("tenants:read")
    assert not perms.allows("tenants:write")


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        parse_school_roles(["director", "superuser"])


@pytest.mark.parametrize(
    "capability", sorted(ROLE_ONLY_CAPABILITIES & {"payments:process", "roles:assign"})
)
def test_role_only_capabilities_cannot_be_granted(capability: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_capabilities([capability])


def test_wildcard_capability_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        validate_custom_capabilities(["*"])


def test_known_capability_is_accepted() -> None:
    assert validate_custom_capabilities(["cbt:read"]) == {"cbt:read"}
