from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings, get_settings


@dataclass
class StorageHealth:
    ok: bool
    message: str
    bucket: str | None = None


class ObjectStorageClient:
    """S3-compatible object storage (Neon-aligned or any S3 endpoint)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: BaseClient | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.object_storage_access_key.strip()
            and self._settings.object_storage_secret_key.strip()
            and self._settings.object_storage_bucket.strip()
        )

    def _client_or_raise(self) -> BaseClient:
        if self._client is not None:
            return self._client
        if not self.configured:
            raise RuntimeError("Object storage credentials are not configured")
        kwargs: dict = {
            "service_name": "s3",
            "aws_access_key_id": self._settings.object_storage_access_key,
            "aws_secret_access_key": self._settings.object_storage_secret_key,
            "region_name": self._settings.object_storage_region or "auto",
        }
        if self._settings.object_storage_endpoint.strip():
            kwargs["endpoint_url"] = self._settings.object_storage_endpoint.strip()
        kwargs["config"] = Config(s3={"addressing_style": "path"})
        self._client = boto3.client(**kwargs)
        return self._client

    @staticmethod
    def object_key(
        *,
        tenant_id: str,
        source_type: str,
        source_id: str,
        version: str,
        filename: str,
    ) -> str:
        return f"{tenant_id}/{source_type}/{source_id}/{version}/{filename}"

    def put_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        client = self._client_or_raise()
        client.put_object(
            Bucket=self._settings.object_storage_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return key

    def put_fileobj(
        self,
        *,
        key: str,
        fileobj: BinaryIO,
        content_type: str = "application/octet-stream",
    ) -> str:
        client = self._client_or_raise()
        client.upload_fileobj(
            fileobj,
            self._settings.object_storage_bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return key

    def get_bytes(self, key: str) -> bytes:
        client = self._client_or_raise()
        response = client.get_object(Bucket=self._settings.object_storage_bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        client = self._client_or_raise()
        client.delete_object(Bucket=self._settings.object_storage_bucket, Key=key)

    def health_check(self) -> StorageHealth:
        bucket = self._settings.object_storage_bucket
        if not self.configured:
            return StorageHealth(ok=False, message="Object storage credentials missing", bucket=bucket)
        try:
            client = self._client_or_raise()
            client.head_bucket(Bucket=bucket)
            return StorageHealth(ok=True, message="Bucket reachable", bucket=bucket)
        except ClientError as exc:
            return StorageHealth(ok=False, message=str(exc), bucket=bucket)
        except Exception as exc:
            return StorageHealth(ok=False, message=str(exc), bucket=bucket)
