"""Run the API: ``python -m app.presentation.http`` (production uses the Docker CMD)."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.presentation.http.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container networking
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
        loop="uvloop",
        http="httptools",
        access_log=False,
    )


if __name__ == "__main__":
    main()
