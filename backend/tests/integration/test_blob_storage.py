import os
from uuid import uuid4

import pytest

from app.config import Settings
from app.storage.s3 import S3Storage

pytestmark = pytest.mark.integration


def test_rustfs_immutable_bytes_and_hash_survive_new_client():
    endpoint = os.getenv("TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("TEST_S3_ENDPOINT_URL is not configured")
    settings = Settings(
        _env_file=None,
        storage_backend="s3",
        s3_endpoint_url=endpoint,
        s3_access_key=os.environ["TEST_S3_ACCESS_KEY"],
        s3_secret_key=os.environ["TEST_S3_SECRET_KEY"],
        s3_bucket="test-" + uuid4().hex,
    )
    storage = S3Storage(settings)
    storage.initialize()
    keys = [f"originals/{uuid4()}/file.csv", f"derived/{uuid4()}/result.csv"]
    try:
        first = storage.put(keys[0], b"original\n")
        result = storage.put(keys[1], b"25000.00\n")
        assert storage.put(first.key, b"original\n") == first
        with pytest.raises(ValueError):
            storage.put(first.key, b"mutated\n")
        assert S3Storage(settings).verify(first.key, first.sha256, first.byte_size)
        assert storage.verify(result.key, result.sha256, result.byte_size)
        with pytest.raises(ValueError):
            storage.read(first.key, max_bytes=2)
        assert storage.ready()
        with pytest.raises(ValueError):
            storage.put("originals/../escape", b"bad")
    finally:
        for key in keys:
            storage.client.delete_object(Bucket=settings.s3_bucket, Key=key)
        storage.client.delete_bucket(Bucket=settings.s3_bucket)
