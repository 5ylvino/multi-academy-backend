"""Object storage: the SigV4 signer against AWS's published examples, and the local store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.domain.shared.errors import NotFound
from app.infrastructure.storage.local import LocalObjectStore
from app.infrastructure.storage.s3 import S3ObjectStore, StorageError
from app.infrastructure.storage.sigv4 import EMPTY_SHA256, sign

# From the AWS S3 documentation, "Signature Calculations for the Authorization Header".
KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AT = datetime(2013, 5, 24, tzinfo=UTC)


def test_sigv4_get_object_example() -> None:
    auth = sign(
        method="GET",
        path="/test.txt",
        query={},
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": "20130524T000000Z",
        },
        payload_sha256=EMPTY_SHA256,
        access_key=KEY,
        secret_key=SECRET,
        region="us-east-1",
        at=AT,
    )
    assert auth == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_sigv4_put_object_example() -> None:
    body_hash = "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"
    auth = sign(
        method="PUT",
        path="/test$file.text",
        query={},
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": "20130524T000000Z",
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": body_hash,
        },
        payload_sha256=body_hash,
        access_key=KEY,
        secret_key=SECRET,
        region="us-east-1",
        at=AT,
    )
    assert auth.endswith(
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    )


async def test_s3_store_signs_path_style_requests() -> None:
    seen: list[httpx.Request] = []
    stored: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            stored[request.url.path] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            body = stored.get(request.url.path)
            return httpx.Response(200, content=body) if body else httpx.Response(404)
        return httpx.Response(204)

    store = S3ObjectStore(
        endpoint="https://br-test.storage.c-2.us-east-2.aws.neon.tech",
        bucket="masms-private",
        region="us-east-2",
        access_key="nak_test",
        secret_key="nsk_test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await store.put("tenants/t/payment-proofs/p.pdf", b"%PDF-data", content_type="application/pdf")
    assert await store.get("tenants/t/payment-proofs/p.pdf") == b"%PDF-data"
    with pytest.raises(NotFound):
        await store.get("missing.pdf")
    await store.delete("tenants/t/payment-proofs/p.pdf")
    put = seen[0]
    assert put.url.path == "/masms-private/tenants/t/payment-proofs/p.pdf"
    assert put.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=nak_test/")
    assert "/us-east-2/s3/aws4_request" in put.headers["authorization"]
    assert put.headers["x-amz-content-sha256"] != EMPTY_SHA256
    await store.aclose()

    failing = S3ObjectStore(
        endpoint="https://example.test",
        bucket="b",
        region="r",
        access_key="a",
        secret_key="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with pytest.raises(StorageError):
        await failing.put("k", b"x", content_type="application/pdf")
    with pytest.raises(ValueError, match="HTTPS"):
        S3ObjectStore(
            endpoint="http://example.test", bucket="b", region="r", access_key="a", secret_key="s"
        )


async def test_local_store_round_trip_and_refuses_escapes(tmp_path: Path) -> None:
    store = LocalObjectStore(str(tmp_path))
    await store.put("a/b/c.pdf", b"data", content_type="application/pdf")
    assert await store.get("a/b/c.pdf") == b"data"
    await store.delete("a/b/c.pdf")
    with pytest.raises(NotFound):
        await store.get("a/b/c.pdf")
    with pytest.raises(ValueError, match="escapes"):
        await store.put("../../etc/passwd", b"x", content_type="text/plain")
