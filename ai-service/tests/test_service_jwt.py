import pytest

from app.security.service_jwt import ServiceJwtError, build_request_context, decode_service_token, issue_service_token


def test_issue_and_decode_service_token() -> None:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="user-1",
        roles=["teacher"],
        features=["ai.assistant"],
    )
    payload = decode_service_token(token)
    assert payload["tenant_id"] == "tenant-1"
    assert payload["actor_id"] == "user-1"
    assert payload["roles"] == ["teacher"]


def test_tenant_header_mismatch_raises() -> None:
    payload = decode_service_token(
        issue_service_token(tenant_id="tenant-1", actor_id="user-1"),
    )
    with pytest.raises(ServiceJwtError, match="Tenant header"):
        build_request_context(payload, header_tenant_id="tenant-2")


def test_invalid_token_raises() -> None:
    with pytest.raises(ServiceJwtError):
        decode_service_token("not-a-jwt")
