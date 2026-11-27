"""Access to the process container and request client details."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.infrastructure.container import Container, UseCases
from app.use_cases.identity.session_issuer import ClientInfo


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_use_cases(container: Annotated[Container, Depends(get_container)]) -> UseCases:
    return container.use_cases


def client_ip(request: Request) -> str | None:
    """Client IP, trusting only the configured number of proxy hops in X-Forwarded-For."""
    container: Container = request.app.state.container
    hops = container.settings.trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and hops > 0:
        chain = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return request.client.host if request.client else None


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(ip_address=client_ip(request), user_agent=request.headers.get("user-agent"))


UseCasesDep = Annotated[UseCases, Depends(get_use_cases)]
ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]
