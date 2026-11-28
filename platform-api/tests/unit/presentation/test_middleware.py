from __future__ import annotations

from typing import Any

from app.presentation.http.middleware import _route_template


class _Route:
    def __init__(self, path: str) -> None:
        self.path = path


def scope(path: str, template: str | None) -> dict[str, Any]:
    s: dict[str, Any] = {"path": path}
    if template is not None:
        s["route"] = _Route(template)
    return s


def test_rebuilds_static_prefix_of_included_routers() -> None:
    got = _route_template(scope("/api/v1/platform/tenants/abc", "/platform/tenants/{tenant_id}"))  # type: ignore[arg-type]
    assert got == "/api/v1/platform/tenants/{tenant_id}"


def test_top_level_route_is_unchanged() -> None:
    assert _route_template(scope("/health/live", "/health/live")) == "/health/live"  # type: ignore[arg-type]


def test_unmatched() -> None:
    assert _route_template(scope("/nope", None)) == "unmatched"  # type: ignore[arg-type]
