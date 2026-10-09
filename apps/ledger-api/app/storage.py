"""Original invoice files, in S3-compatible object storage (Cloudflare R2) or on local disk.

Layout: {prefix}/{org_id}/invoices/{invoice_id}.pdf. Each org's files live under its own
prefix, and the key is stored on the invoice row, which is itself scoped to the org.
"""

import asyncio
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import settings


class StorageError(Exception):
    pass


def invoice_key(org_id: uuid.UUID, invoice_id: uuid.UUID) -> str:
    return f"{settings.storage_prefix}/{org_id}/invoices/{invoice_id}.pdf"


class Storage(Protocol):
    async def put(
        self, key: str, data: bytes, content_type: str, metadata: dict[str, str]
    ) -> None: ...
    async def get(self, key: str) -> bytes: ...


class LocalStorage:
    """For development without object-storage credentials."""

    def __init__(self, root: str):
        self.root = Path(root)

    async def put(self, key: str, data: bytes, content_type: str, metadata: dict[str, str]) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread((self.root / key).read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"file not found: {key}") from exc


class S3Storage:
    """Any S3-compatible store. For Cloudflare R2 the region is 'auto'."""

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def put(self, key: str, data: bytes, content_type: str, metadata: dict[str, str]) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            # boto3 is synchronous; keep it off the event loop.
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=metadata,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"upload failed: {exc}") from exc

    async def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        def _read() -> bytes:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        try:
            return await asyncio.to_thread(_read)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"download failed: {exc}") from exc


@lru_cache
def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(settings.storage_dir)
