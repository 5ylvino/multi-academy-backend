from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.tutoring import TutorProfileResponse, TutorSearchResponse
from app.security.service_jwt import issue_service_token

client = TestClient(app)


def _headers(features: list[str], roles: list[str] | None = None) -> dict[str, str]:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="teacher-1",
        roles=roles or ["teacher"],
        features=features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"}


def test_register_requires_feature() -> None:
    res = client.post(
        "/v1/tutors/register",
        headers=_headers([]),
        json={"displayName": "Mr Okafor", "subjects": ["Mathematics"], "hourlyRate": 5000},
    )
    assert res.status_code == 403


def test_register_success() -> None:
    mock = TutorProfileResponse(
        tutorId="t1",
        userId="teacher-1",
        displayName="Mr Okafor",
        subjects=["Mathematics"],
        hourlyRate=5000,
        currency="NGN",
        visibility="school",
        isSchoolTeacher=True,
        isExternal=False,
        status="active",
        ratingAvg=0,
        sessionCount=0,
    )
    with patch("app.api.v1.tutors.TutorService.register", return_value=mock):
        res = client.post(
            "/v1/tutors/register",
            headers=_headers(["tutoring.marketplace"]),
            json={"displayName": "Mr Okafor", "subjects": ["Mathematics"], "hourlyRate": 5000},
        )
    assert res.status_code == 200
    assert res.json()["displayName"] == "Mr Okafor"


def test_search_success() -> None:
    mock = TutorSearchResponse(items=[], total=0)
    with patch("app.api.v1.tutors.TutorService.search", return_value=mock):
        res = client.get(
            "/v1/tutors/search?subject=Mathematics",
            headers=_headers(["tutoring.marketplace"], roles=["parent"]),
        )
    assert res.status_code == 200
