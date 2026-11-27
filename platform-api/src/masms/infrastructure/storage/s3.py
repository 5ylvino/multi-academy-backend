"""S3-compatible object storage over HTTPS, for Neon Object Storage.

Neon gives each branch an S3 endpoint and expects path-style addressing
(``https://<endpoint>/<bucket>/<key>``). The same adapter works with AWS S3 or R2.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from masms.domain.shared.errors import NotFound
from masms.infrastructure.storage.sigv4 import EMPTY_SHA256, sign


class StorageError(RuntimeError):
    pass


class S3ObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Object storage endpoint must use HTTPS.")
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._host = parts.netloc
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _path(self, key: str) -> str:
        return f"/{self._bucket}/{key.lstrip('/')}"

    async def _send(
        self, method: str, key: str, body: bytes = b"", content_type: str | None = None
    ) -> httpx.Response:
        path = self._path(key)
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        now = datetime.now(UTC)
        headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ"),
        }
        if content_type:
            headers["content-type"] = content_type
        headers["authorization"] = sign(
            method=method,
            path=path,
            query={},
            headers=headers,
            payload_sha256=payload_hash,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            at=now,
        )
        url = f"{self._base}{httpx.URL(path).raw_path.decode()}"
        return await self._client.request(method, url, headers=headers, content=body or None)

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        resp = await self._send("PUT", key, data, content_type)
        if resp.status_code not in {200, 201}:
            raise StorageError(f"Object storage PUT failed with HTTP {resp.status_code}.")

    async def get(self, key: str) -> bytes:
        resp = await self._send("GET", key)
        if resp.status_code == 404:
            raise NotFound("File not found.")
        if resp.status_code != 200:
            raise StorageError(f"Object storage GET failed with HTTP {resp.status_code}.")
        return resp.content

    async def delete(self, key: str) -> None:
        resp = await self._send("DELETE", key)
        if resp.status_code not in {200, 204, 404}:
            raise StorageError(f"Object storage DELETE failed with HTTP {resp.status_code}.")
