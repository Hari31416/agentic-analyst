from datetime import datetime
from typing import Protocol

from app.contracts import Contract


class SessionInfo(Contract):
    id: str
    backend: str
    status: str
    network: str | None = None
    expires_at: datetime | None = None


class ExecutionInfo(Contract):
    id: str
    status: str
    exit_code: int | None
    session_id: str | None = None


class FileEntry(Contract):
    path: str
    is_dir: bool
    size_bytes: int
    updated_at: datetime | None = None


class ArtifactExportInfo(Contract):
    id: str
    session_id: str
    source_path: str
    artifact_uri: str
    size_bytes: int
    sha256: str


class Sandbox(Protocol):
    async def create(self, workspace_id: str, run_id: str) -> SessionInfo: ...
    async def execute(
        self, session_id: str, command: str, timeout_seconds: int
    ) -> ExecutionInfo: ...
    async def write(self, session_id: str, path: str, content: bytes) -> None: ...
    async def read(self, session_id: str, path: str) -> bytes: ...
    async def stop(self, session_id: str) -> None: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def heartbeat(
        self, session_id: str, extend_seconds: int | None = None
    ) -> SessionInfo: ...
    async def status(self, session_id: str) -> SessionInfo: ...
    async def list_files(self, session_id: str, path: str = "") -> list[FileEntry]: ...
    async def sync_to_artifacts(
        self, session_id: str, paths: list[str], destination_prefix: str
    ) -> list[ArtifactExportInfo]: ...
    async def stdout(
        self, session_id: str, execution_id: str, max_bytes: int
    ) -> bytes: ...
    async def stderr(
        self, session_id: str, execution_id: str, max_bytes: int
    ) -> bytes: ...
