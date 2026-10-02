import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.storage.keys import validate_key


@dataclass(frozen=True)
class StoredFile:
    key: str
    byte_size: int
    sha256: str


class Storage(Protocol):
    def put(self, key: str, content: bytes) -> StoredFile: ...
    def read(self, key: str, max_bytes: int | None = None) -> bytes: ...


class FileStorage:
    """Immutable objects. Originals and generated outputs occupy separate namespaces."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        validate_key(key)
        parts = PurePosixPath(key).parts
        path = self.root.joinpath(*parts)
        for parent in [path, *path.parents]:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("storage symlinks are prohibited")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("storage path escaped root")
        return path

    def put(self, key: str, content: bytes) -> StoredFile:
        path = self.path(key)
        digest = hashlib.sha256(content).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".pending-", delete=False
            ) as temp:
                temp_path = Path(temp.name)
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
                os.chmod(temp.name, 0o440)
            # A hard link publishes completed bytes atomically without overwriting an original.
            try:
                os.link(temp_path, path)
            except FileExistsError:
                if self.read(key) != content:
                    raise ValueError(
                        "immutable storage key already contains different bytes"
                    )
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return StoredFile(key, len(content), digest)

    def read(self, key: str, max_bytes: int | None = None) -> bytes:
        path = self.path(key)
        with path.open("rb") as stream:
            if max_bytes is not None:
                content = stream.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise ValueError("stored object exceeds read limit")
                return content
            return stream.read()

    def verify(self, key: str, sha256: str, byte_size: int) -> bool:
        content = self.read(key, byte_size)
        return (
            len(content) == byte_size and hashlib.sha256(content).hexdigest() == sha256
        )

    def ready(self) -> bool:
        # Probe atomic writes without publishing an original or a derived artifact.
        with tempfile.TemporaryFile(dir=self.root) as stream:
            stream.write(b"ready")
            stream.flush()
            os.fsync(stream.fileno())
        return True
