import base64
import hashlib
from typing import Any
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.storage.filesystem import StoredFile
from app.storage.keys import validate_key


class StorageUnavailable(RuntimeError):
    pass


class S3Storage:
    def __init__(self, settings: Settings, client: Any = None):
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        self.client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=(
                    settings.s3_access_key.get_secret_value()
                    if settings.s3_access_key
                    else ""
                ),
                aws_secret_access_key=(
                    settings.s3_secret_key.get_secret_value()
                    if settings.s3_secret_key
                    else ""
                ),
                region_name=settings.s3_region,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=3,
                    read_timeout=15,
                    retries={"max_attempts": 2},
                    s3={"addressing_style": "path"},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        )

    def initialize(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
                raise StorageUnavailable("S3 bucket is unavailable") from exc
            options: dict[str, Any] = {"Bucket": self.bucket}
            if self.region != "us-east-1":
                options["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.region
                }
            try:
                self.client.create_bucket(**options)
            except ClientError as create_exc:
                if create_exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                    raise StorageUnavailable(
                        "S3 bucket could not be initialized"
                    ) from create_exc
        except BotoCoreError as exc:
            raise StorageUnavailable("S3 endpoint is unavailable") from exc

    def put(self, key: str, content: bytes) -> StoredFile:
        validate_key(key)
        digest = hashlib.sha256(content).hexdigest()
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                IfNoneMatch="*",
                Metadata={"sha256": digest},
                ContentMD5=base64.b64encode(
                    hashlib.md5(content, usedforsecurity=False).digest()
                ).decode(),
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                if self.read(key, len(content)) != content:
                    raise ValueError(
                        "immutable storage key already contains different bytes"
                    ) from exc
            else:
                raise StorageUnavailable("S3 write failed") from exc
        except BotoCoreError as exc:
            raise StorageUnavailable("S3 endpoint is unavailable") from exc
        return StoredFile(key, len(content), digest)

    def read(self, key: str, max_bytes: int | None = None) -> bytes:
        validate_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                if max_bytes is not None and response["ContentLength"] > max_bytes:
                    raise ValueError("stored object exceeds read limit")
                content: bytes = (
                    stream.read() if max_bytes is None else stream.read(max_bytes + 1)
                )
                if max_bytes is not None and len(content) > max_bytes:
                    raise ValueError("stored object exceeds read limit")
                return content
            finally:
                stream.close()
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailable("S3 object is unavailable") from exc

    def verify(self, key: str, sha256: str, byte_size: int) -> bool:
        content = self.read(key, byte_size)
        return (
            len(content) == byte_size and hashlib.sha256(content).hexdigest() == sha256
        )

    def ready(self) -> bool:
        key = f"derived/readiness/{uuid4().hex}"
        try:
            item = self.put(key, b"storage-ready")
            return self.verify(key, item.sha256, item.byte_size)
        finally:
            try:
                self.client.delete_object(Bucket=self.bucket, Key=key)
            except (ClientError, BotoCoreError):
                pass
