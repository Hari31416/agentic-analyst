from app.config import get_settings
from app.storage.factory import get_storage
from app.storage.s3 import S3Storage

if __name__ == "__main__":
    storage = get_storage(get_settings())
    if isinstance(storage, S3Storage):
        storage.initialize()
    storage.ready()
    print("Blob storage initialized")
