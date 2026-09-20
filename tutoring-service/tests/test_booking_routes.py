from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.tutoring import BookingListResponse
from app.security.service_jwt import issue_service_token

client = TestClient(app)


def _headers(features: list[str], roles: list[str] | None = None) -> dict[str, str]:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="parent-1",
        roles=roles or ["parent"],
        features=features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"}


def test_list_bookings_requires_feature() -> None:
    res = client.get("/v1/bookings/mine", headers=_headers([]))
    assert res.status_code == 403


def test_list_bookings_success() -> None:
    mock = BookingListResponse(items=[], total=0)
    with patch("app.api.v1.bookings.BookingService.list_mine", return_value=mock):
        res = client.get("/v1/bookings/mine", headers=_headers(["tutoring.marketplace"]))
    assert res.status_code == 200
    assert res.json()["total"] == 0


def test_unverified_payment_confirm_route_is_not_available() -> None:
    res = client.post(
        "/v1/payments/00000000-0000-0000-0000-000000000000/confirm",
        headers=_headers(["tutoring.payments"]),
    )
    assert res.status_code == 404
