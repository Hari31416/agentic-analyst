"""Run model-authored Python only in the pinned, network-disabled microVM."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from pathlib import PurePosixPath
from typing import Literal, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from app.contracts import ArtifactInfo, SafeError, ToolResult
from app.sandbox.client import (
    SandboxError,
    SandboxExecutionAmbiguous,
    SandboxHTTPClient,
)
from app.sandbox.protocol import ExecutionInfo, FileEntry, SessionInfo
from app.storage.filesystem import Storage
from app.storage.keys import validate_key

_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_MAX_CODE_BYTES = 256 * 1024
_MAX_OUTPUT_FILES = 16
_MAX_OUTPUT_FILE_BYTES = 8 * 1024 * 1024
_MAX_CONSOLE_BYTES = 64 * 1024
_LAUNCHER_PREFIX = """from pathlib import Path
import os
import runpy

workspace = Path('/workspace')
out = workspace / 'outputs' / {call_id!r}
out.mkdir(parents=True, exist_ok=True)
os.chdir(out)
runpy.run_path(str(workspace / '.agent' / 'program.py'), run_name='__main__')
"""


def _component(value: str, label: str) -> str:
    if not _SAFE_PART.fullmatch(value):
        raise ValueError(f"unsafe {label}")
    return value


def _output_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("output paths must be nonempty relative paths")
    candidate = value.removeprefix("outputs/")
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "/".join(path.parts) != candidate
    ):
        raise ValueError("output paths must stay inside outputs/")
    if any(not _SAFE_PART.fullmatch(part) for part in path.parts):
        raise ValueError("output path contains an unsupported character")
    return candidate


def _text(data: bytes, limit: int) -> tuple[str, bool]:
    clipped = data[:limit]
    truncated = len(data) > limit
    result = clipped.decode("utf-8", errors="replace")
    if truncated:
        result += "\n[output truncated]"
    return result, truncated


def _media_type(filename: str, content: bytes) -> str:
    # Avoid telling a browser to execute active content from a guest artifact.
    guessed, _ = mimetypes.guess_type(filename, strict=False)
    if guessed in {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/javascript",
        "application/javascript",
    }:
        return "application/octet-stream"
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"PK\x03\x04", "application/zip"),
    )
    for signature, media_type in signatures:
        if content.startswith(signature):
            return media_type
    if guessed and guessed.startswith("image/"):
        return "application/octet-stream"
    if guessed and guessed.startswith("text/"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return guessed
    return guessed or "application/octet-stream"


class PythonExecution:
    """One serialized microVM session owned by a run worker."""

    def __init__(
        self,
        client: SandboxHTTPClient,
        storage: Storage,
        workspace_id: str,
        run_id: str,
        *,
        max_output_bytes: int = _MAX_OUTPUT_FILE_BYTES,
        max_output_files: int = _MAX_OUTPUT_FILES,
        max_console_bytes: int = _MAX_CONSOLE_BYTES,
    ) -> None:
        self.client = client
        self.storage = storage
        self.workspace_id = _component(workspace_id, "workspace ID")
        self.run_id = _component(run_id, "run ID")
        try:
            self._run_uuid = UUID(self.run_id)
        except ValueError as error:
            raise ValueError("run ID must be a UUID") from error
        if min(max_output_bytes, max_output_files, max_console_bytes) <= 0:
            raise ValueError("Python output limits must be positive")
        self.max_output_bytes = min(max_output_bytes, _MAX_OUTPUT_FILE_BYTES)
        self.max_output_files = min(max_output_files, _MAX_OUTPUT_FILES)
        self.max_console_bytes = min(max_console_bytes, _MAX_CONSOLE_BYTES)
        self.session: SessionInfo | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.cancelled_result: ToolResult | None = None

    async def _ensure_session(self) -> SessionInfo:
        if self._closed:
            raise RuntimeError("Python execution session is closed")
        if self.session is None:
            self.session = await self.client.create(self.workspace_id, self.run_id)
        return self.session

    async def execute(
        self,
        code: str,
        tool_call_id: str,
        output_paths: Sequence[str] | None = None,
        *,
        timeout_seconds: int = 120,
    ) -> ToolResult:
        call_id = _component(tool_call_id, "tool call ID")
        try:
            UUID(call_id)
        except ValueError as error:
            raise ValueError("tool call ID must be a UUID") from error
        if not isinstance(code, str):
            raise ValueError("Python source must be text")
        code_bytes = code.encode("utf-8")
        if not code_bytes or len(code_bytes) > _MAX_CODE_BYTES:
            raise ValueError("Python source must be between 1 byte and 256 KiB")
        expected: set[str] | None = None
        if output_paths is not None:
            if len(output_paths) > self.max_output_files:
                raise ValueError("too many requested output files")
            expected = {_output_relative_path(item) for item in output_paths}
            if len(expected) != len(output_paths):
                raise ValueError("output paths must be unique")
        if timeout_seconds < 1 or timeout_seconds > 300:
            raise ValueError("Python timeout must be from 1 to 300 seconds")

        async with self._lock:
            session = await self._ensure_session()
            try:
                await self.client.heartbeat(session.id)
                await self.client.write(session.id, ".agent/program.py", code_bytes)
                launcher = _LAUNCHER_PREFIX.format(call_id=call_id).encode("utf-8")
                await self.client.write(session.id, ".agent/launcher.py", launcher)
                await self.client.write(session.id, f"outputs/{call_id}/.keep", b"")
                execution = await self.client.execute(
                    session.id,
                    "python -I /workspace/.agent/launcher.py",
                    timeout_seconds,
                )
                ambiguous = False
            except SandboxExecutionAmbiguous as error:
                await self.client.reconcile_ambiguous_execution(error)
                execution = None
                ambiguous = True
            except asyncio.CancelledError:
                # Exec is synchronous at the service boundary. Best-effort read
                # files that already exist before stopping the VM; the worker can
                # persist this result while recording the run as cancelled.
                try:
                    artifacts, collection_errors = await self._collect_outputs(
                        session.id, call_id, expected
                    )
                    self.cancelled_result = ToolResult(
                        status="partial" if artifacts else "failed",
                        summary="Python execution was cancelled; existing sandbox outputs were retained.",
                        artifact_ids=[UUID(str(item["id"])) for item in artifacts],
                        error=SafeError(
                            code="python_execution_cancelled",
                            message="Execution was cancelled; collected outputs may be partial.",
                            retryable=False,
                        ),
                        data={
                            "session_id": session.id,
                            "execution_outcome_unknown": True,
                            "artifacts": artifacts,
                            "collection_errors": collection_errors,
                        },
                    )
                finally:
                    try:
                        await self.client.stop(session.id)
                    except SandboxError:
                        pass
                raise
            except SandboxError as error:
                return ToolResult(
                    status="failed",
                    summary="Python execution could not be completed in the sandbox.",
                    error=SafeError(
                        code=error.code, message=str(error), retryable=error.retryable
                    ),
                    data={
                        "session_id": session.id,
                        "execution_outcome_unknown": error.ambiguous,
                    },
                )

            stdout = b""
            stderr = b""
            console_error = False
            if execution is not None:
                try:
                    stdout = await self.client.stdout(
                        session.id, execution.id, self.max_console_bytes
                    )
                    stderr = await self.client.stderr(
                        session.id, execution.id, self.max_console_bytes
                    )
                except SandboxError:
                    console_error = True
            artifacts, collection_errors = await self._collect_outputs(
                session.id, call_id, expected
            )
            if console_error:
                collection_errors.append("console_output_read_failed")
            stdout_text, stdout_truncated = _text(stdout, self.max_console_bytes)
            stderr_text, stderr_truncated = _text(stderr, self.max_console_bytes)
            execution_status = (
                "unknown"
                if ambiguous
                else execution.status if execution else "unavailable"
            )
            successful = (
                not ambiguous
                and execution is not None
                and execution.status == "completed"
                and execution.exit_code == 0
            )
            status: Literal["ok", "partial", "failed"]
            if successful and not collection_errors:
                status = "ok"
            elif artifacts or ambiguous or (successful and collection_errors):
                status = "partial"
            else:
                status = "failed"
            summary = (
                "Python ran successfully in an isolated microVM."
                if successful
                else (
                    "Python execution outcome is unknown; the command was not repeated."
                    if ambiguous
                    else "Python ran in the isolated microVM but returned a failure status."
                )
            )
            if artifacts:
                summary += f" Collected {len(artifacts)} output artifact(s)."
            tool_error: SafeError | None = None
            if ambiguous:
                tool_error = SafeError(
                    code="sandbox_execution_ambiguous",
                    message="The execution request may have completed; it was not retried.",
                    retryable=False,
                )
            elif collection_errors:
                tool_error = SafeError(
                    code="sandbox_output_collection_partial",
                    message="Some sandbox outputs could not be safely collected.",
                    retryable=False,
                )
            elif not successful:
                tool_error = SafeError(
                    code="python_execution_failed",
                    message=f"Sandbox execution finished with status {execution_status} and exit code {execution.exit_code if execution else 'unknown'}.",
                    retryable=False,
                )
            return ToolResult(
                status=status,
                summary=summary,
                artifact_ids=[UUID(str(item["id"])) for item in artifacts],
                error=tool_error,
                data={
                    "session_id": session.id,
                    "execution": {
                        "id": execution.id if execution else None,
                        "status": execution_status,
                        "exit_code": execution.exit_code if execution else None,
                    },
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "artifacts": artifacts,
                    "collection_errors": collection_errors,
                },
            )

    async def _collect_outputs(
        self, session_id: str, call_id: str, expected: set[str] | None
    ) -> tuple[list[dict[str, object]], list[str]]:
        prefix = f"outputs/{call_id}"
        errors: list[str] = []
        result: list[dict[str, object]] = []
        try:
            entries = await self.client.list_files(session_id, prefix)
        except SandboxError:
            return [], ["output_listing_failed"]
        files: list[tuple[str, FileEntry]] = []
        seen: set[str] = set()
        for entry in entries:
            if entry.is_dir or entry.path == f"{prefix}/.keep":
                continue
            if not entry.path.startswith(prefix + "/"):
                errors.append("unsafe_output_path")
                continue
            try:
                relative = _output_relative_path(entry.path.removeprefix(prefix + "/"))
            except ValueError:
                errors.append("unsafe_output_path")
                continue
            if relative in seen:
                errors.append("duplicate_output_path")
                continue
            seen.add(relative)
            if expected is None or relative in expected:
                files.append((relative, entry))
        if len(files) > self.max_output_files:
            files = files[: self.max_output_files]
            errors.append("output_file_count_limit")
        found = {name for name, _ in files}
        if expected is not None and expected - found:
            errors.append("requested_output_missing")
        total = 0
        for relative, entry in files:
            if entry.size_bytes < 0 or entry.size_bytes > self.max_output_bytes:
                errors.append("output_file_size_limit")
                continue
            if total + entry.size_bytes > self.max_output_bytes:
                errors.append("total_output_size_limit")
                break
            try:
                content = await self.client.read(
                    session_id, f"{prefix}/{relative}", max_bytes=self.max_output_bytes
                )
            except SandboxError:
                errors.append("output_read_failed")
                continue
            if len(content) != entry.size_bytes:
                errors.append("output_size_changed")
                continue
            total += len(content)
            digest = hashlib.sha256(content).hexdigest()
            key = f"derived/{self.workspace_id}/{self.run_id}/{call_id}/{relative}"
            try:
                validate_key(key)
                stored = self.storage.put(key, content)
                if stored.byte_size != len(content) or stored.sha256 != digest:
                    raise ValueError("storage write metadata mismatch")
                retained = self.storage.read(key, max_bytes=len(content))
                if (
                    retained != content
                    or hashlib.sha256(retained).hexdigest() != digest
                ):
                    raise ValueError("stored output verification failed")
            except Exception:
                errors.append("durable_output_write_failed")
                continue
            artifact_id = uuid5(NAMESPACE_URL, f"sandbox-artifact:{key}")
            info = ArtifactInfo(
                id=artifact_id,
                storage_key=key,
                media_type=_media_type(relative, content),
                byte_size=len(content),
                sha256=digest,
                run_id=self._run_uuid,
                tool_call_id=UUID(call_id),
                lineage=[f"sandbox:{session_id}", "tool:run_python"],
                durable=True,
            )
            result.append(
                {
                    **info.model_dump(mode="json"),
                    "display_name": PurePosixPath(relative).name,
                    "relative_path": relative,
                }
            )
        return result, errors

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            if self.session is not None:
                try:
                    await self.client.stop(self.session.id)
                finally:
                    await self.client.delete_session(self.session.id)
            self._closed = True

    async def __aenter__(self) -> PythonExecution:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


async def execute_python(
    client: SandboxHTTPClient,
    storage: Storage,
    workspace_id: str,
    run_id: str,
    tool_call_id: str,
    code: str,
    output_paths: Sequence[str] | None = None,
    *,
    timeout_seconds: int = 120,
    max_output_bytes: int = _MAX_OUTPUT_FILE_BYTES,
) -> ToolResult:
    """Single-call convenience API; run workers should retain PythonExecution."""
    execution = PythonExecution(
        client,
        storage,
        workspace_id,
        run_id,
        max_output_bytes=max_output_bytes,
    )
    try:
        return await execution.execute(
            code,
            tool_call_id,
            output_paths,
            timeout_seconds=timeout_seconds,
        )
    finally:
        await execution.aclose()
