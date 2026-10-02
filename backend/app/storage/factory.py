from app.config import Settings, get_settings
from app.storage.filesystem import FileStorage
from app.storage.s3 import S3Storage


def get_storage(settings: Settings | None = None) -> FileStorage | S3Storage:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return FileStorage(settings.storage_root)
