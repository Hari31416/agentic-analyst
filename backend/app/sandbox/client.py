"""HTTP adapter for the pinned Nexus sandbox service.

The request/response contract was checked against standalone sandbox revision
``b3f032b6a0ce1fab7cebc75250073f58b5b6c63c``. In that revision,
``sandbox_service/api/routes/execs.py`` awaits the runtime in the exec POST;
a lost response is therefore ambiguous and is never retried. The service's
``runtime/microsandbox.py`` maps the requested disabled policy to
``Network.none()``.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.sandbox.protocol import (
    ArtifactExportInfo,
    ExecutionInfo,
    FileEntry,
    SessionInfo,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
PINNED_SANDBOX_REVISION = "b3f032b6a0ce1fab7cebc75250073f58b5b6c63c"


class SandboxError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "sandbox_error",
        retryable: bool = False,
        status_code: int | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.ambiguous = ambiguous


class SandboxExecutionAmbiguous(SandboxError):
    """The synchronous exec may have run, but its response was not received."""

    def __init__(self, session_id: str, message: str = "execution outcome is unknown"):
        super().__init__(
            message,
            code="sandbox_execution_ambiguous",
            retryable=False,
            ambiguous=True,
        )
        self.session_id = session_id
        self.session_status: SessionInfo | None = None


class SandboxHTTPClient:
    """Typed client for the service's synchronous REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        image: str,
        auth_token: str | None = None,
        request_timeout_seconds: float = 30.0,
        session_timeout_seconds: int = 900,
        cpu: int = 1,
        memory_mb: int = 1024,
        disk_mb: int = 2048,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("sandbox base URL must use HTTP or HTTPS")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(
                "sandbox base URL must not contain credentials or query strings"
            )
        if not image.strip():
            raise ValueError("sandbox image is required")
        if min(session_timeout_seconds, cpu, memory_mb, disk_mb) <= 0:
            raise ValueError("sandbox limits must be positive")
        self.image = image
        self.session_timeout_seconds = session_timeout_seconds
        headers = {"Accept": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(request_timeout_seconds),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._owns_client = client is None
        self._cpu = cpu
        self._memory_mb = memory_mb
        self._disk_mb = disk_mb

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> SandboxHTTPClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _safe_id(value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("invalid sandbox identifier")
        return quote(value, safe="")

    async def _request(
        self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any
    ) -> httpx.Response:
        try:
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise SandboxError(
                "sandbox request timed out",
                code="sandbox_timeout",
                retryable=False,
            ) from error
        except httpx.HTTPError as error:
            raise SandboxError(
                "sandbox service is unavailable",
                code="sandbox_unavailable",
                retryable=True,
            ) from error
        if response.status_code >= 400:
            retryable = (
                response.status_code in {408, 429} or response.status_code >= 500
            )
            raise SandboxError(
                f"sandbox service returned HTTP {response.status_code}",
                code="sandbox_http_error",
                retryable=retryable,
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _session(data: dict[str, Any]) -> SessionInfo:
        try:
            return SessionInfo.model_validate(
                {
                    "id": data["id"],
                    "backend": data["backend"],
                    "status": data["status"],
                    "network": data.get("limits", {}).get("network"),
                    "expires_at": data.get("expires_at"),
                }
            )
        except Exception as error:
            raise SandboxError(
                "sandbox returned an invalid session response",
                code="sandbox_protocol_error",
            ) from error

    async def create(self, workspace_id: str, run_id: str) -> SessionInfo:
        # Verify network-policy support before allocating any compute resources.
        response = await self._request("GET", "/v1/backends")
        try:
            backends = response.json()["backends"]
            micro = next(item for item in backends if item["name"] == "microsandbox")
        except (ValueError, KeyError, TypeError, StopIteration) as error:
            raise SandboxError(
                "sandbox did not report a microsandbox backend",
                code="sandbox_capability_error",
            ) from error
        if not micro.get("available") or not micro.get("supports_network_policy"):
            raise SandboxError(
                "microsandbox backend or network policy is unavailable",
                code="sandbox_capability_error",
            )

        payload = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "image": self.image,
            "backend": "microsandbox",
            "limits": {
                "cpu": self._cpu,
                "memory_mb": self._memory_mb,
                "disk_mb": self._disk_mb,
                "timeout_seconds": self.session_timeout_seconds,
                "network": "disabled",
                "allowed_hosts": [],
            },
            "metadata": {"owner": "agentic-rag-analyst", "purpose": "python-tool"},
        }
        response = await self._request("POST", "/v1/sessions", json=payload)
        try:
            session = self._session(response.json())
        except ValueError as error:
            raise SandboxError(
                "sandbox returned invalid JSON for session",
                code="sandbox_protocol_error",
            ) from error
        if (
            session.backend != "microsandbox"
            or session.status != "active"
            or session.network != "disabled"
        ):
            try:
                await self.stop(session.id)
            finally:
                raise SandboxError(
                    "sandbox did not honor the microsandbox/network-disabled request",
                    code="sandbox_policy_error",
                )
        return session

    async def status(self, session_id: str) -> SessionInfo:
        sid = self._safe_id(session_id)
        response = await self._request("GET", f"/v1/sessions/{sid}")
        try:
            return self._session(response.json())
        except ValueError as error:
            raise SandboxError(
                "sandbox returned invalid JSON for session status",
                code="sandbox_protocol_error",
            ) from error

    async def heartbeat(
        self, session_id: str, extend_seconds: int | None = None
    ) -> SessionInfo:
        sid = self._safe_id(session_id)
        payload = (
            {"extend_seconds": extend_seconds} if extend_seconds is not None else {}
        )
        response = await self._request(
            "POST", f"/v1/sessions/{sid}/heartbeat", json=payload
        )
        try:
            return self._session(response.json())
        except ValueError as error:
            raise SandboxError(
                "sandbox returned invalid heartbeat response",
                code="sandbox_protocol_error",
            ) from error

    async def execute(
        self, session_id: str, command: str, timeout_seconds: int
    ) -> ExecutionInfo:
        sid = self._safe_id(session_id)
        if timeout_seconds <= 0:
            raise ValueError("command timeout must be positive")
        payload = {
            "command": command,
            "cwd": "/workspace",
            "timeout_seconds": timeout_seconds,
            "env": {},
        }
        try:
            response = await self._request(
                "POST",
                f"/v1/sessions/{sid}/execs",
                json=payload,
                timeout=timeout_seconds + 30.0,
            )
        except SandboxError as error:
            # The service runs the command inside this synchronous POST. Once the
            # request may have reached it, never retry it automatically.
            if error.status_code is None or error.status_code >= 500:
                raise SandboxExecutionAmbiguous(session_id) from error
            raise
        try:
            data = response.json()
            return ExecutionInfo.model_validate(
                {
                    "id": data["id"],
                    "status": data["status"],
                    "exit_code": data.get("exit_code"),
                    "session_id": data.get("session_id"),
                }
            )
        except Exception as error:
            raise SandboxExecutionAmbiguous(
                session_id, "sandbox completed a request with an unreadable response"
            ) from error

    async def stdout(self, session_id: str, execution_id: str, max_bytes: int) -> bytes:
        return await self._exec_output(session_id, execution_id, "stdout", max_bytes)

    async def stderr(self, session_id: str, execution_id: str, max_bytes: int) -> bytes:
        return await self._exec_output(session_id, execution_id, "stderr", max_bytes)

    async def _exec_output(
        self, session_id: str, execution_id: str, stream: str, max_bytes: int
    ) -> bytes:
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        sid, eid = self._safe_id(session_id), self._safe_id(execution_id)
        path = f"/v1/sessions/{sid}/execs/{eid}/{stream}"
        data = bytearray()
        try:
            async with self._client.stream("GET", path) as response:
                if response.status_code >= 400:
                    retryable = (
                        response.status_code in {408, 429}
                        or response.status_code >= 500
                    )
                    raise SandboxError(
                        f"sandbox service returned HTTP {response.status_code}",
                        code="sandbox_http_error",
                        retryable=retryable,
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    data.extend(chunk[: max_bytes + 1 - len(data)])
                    if len(data) > max_bytes:
                        break
        except SandboxError:
            raise
        except httpx.TimeoutException as error:
            raise SandboxError(
                "sandbox output read timed out", code="sandbox_timeout"
            ) from error
        except httpx.HTTPError as error:
            raise SandboxError(
                "sandbox output is unavailable",
                code="sandbox_unavailable",
                retryable=True,
            ) from error
        return bytes(data)

    async def write(self, session_id: str, path: str, content: bytes) -> dict[str, Any]:
        sid = self._safe_id(session_id)
        response = await self._request(
            "PUT",
            f"/v1/sessions/{sid}/files",
            json={
                "path": path,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "mode": "0644",
            },
        )
        try:
            metadata = response.json()
        except ValueError as error:
            raise SandboxError(
                "sandbox returned invalid file metadata", code="sandbox_protocol_error"
            ) from error
        if not isinstance(metadata, dict):
            raise SandboxError(
                "sandbox returned invalid file metadata", code="sandbox_protocol_error"
            )
        return metadata

    async def read(
        self, session_id: str, path: str, max_bytes: int | None = None
    ) -> bytes:
        sid = self._safe_id(session_id)
        url = f"/v1/sessions/{sid}/files"
        if max_bytes is None:
            response = await self._request("GET", url, params={"path": path})
            return response.content
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        data = bytearray()
        try:
            async with self._client.stream(
                "GET", url, params={"path": path}
            ) as response:
                if response.status_code >= 400:
                    retryable = (
                        response.status_code in {408, 429}
                        or response.status_code >= 500
                    )
                    raise SandboxError(
                        f"sandbox service returned HTTP {response.status_code}",
                        code="sandbox_http_error",
                        retryable=retryable,
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    data.extend(chunk[: max_bytes + 1 - len(data)])
                    if len(data) > max_bytes:
                        raise SandboxError(
                            "sandbox output exceeded the file limit",
                            code="sandbox_output_too_large",
                        )
        except SandboxError:
            raise
        except httpx.TimeoutException as error:
            raise SandboxError(
                "sandbox file read timed out", code="sandbox_timeout"
            ) from error
        except httpx.HTTPError as error:
            raise SandboxError(
                "sandbox file is unavailable",
                code="sandbox_unavailable",
                retryable=True,
            ) from error
        return bytes(data)

    async def list_files(self, session_id: str, path: str = "") -> list[FileEntry]:
        sid = self._safe_id(session_id)
        response = await self._request(
            "GET", f"/v1/sessions/{sid}/files/list", params={"path": path}
        )
        try:
            return [FileEntry.model_validate(item) for item in response.json()]
        except Exception as error:
            raise SandboxError(
                "sandbox returned invalid file listing", code="sandbox_protocol_error"
            ) from error

    async def sync_to_artifacts(
        self, session_id: str, paths: list[str], destination_prefix: str
    ) -> list[ArtifactExportInfo]:
        sid = self._safe_id(session_id)
        if not paths or len(paths) > 64:
            raise ValueError("artifact export needs between 1 and 64 paths")
        safe_paths = [self._safe_relative_path(path) for path in paths]
        prefix = self._safe_relative_path(destination_prefix)
        response = await self._request(
            "POST",
            f"/v1/sessions/{sid}/artifacts/sync",
            json={"paths": safe_paths, "destination_prefix": prefix},
        )
        try:
            values = response.json()
            # The service returns metadata including URIs. Callers must still
            # read and hash file bytes through /files before storing artifacts.
            return [
                ArtifactExportInfo.model_validate(
                    {
                        "id": value["id"],
                        "session_id": value["session_id"],
                        "source_path": value["source_path"],
                        "artifact_uri": value["artifact_uri"],
                        "size_bytes": value["size_bytes"],
                        "sha256": value["sha256"],
                    }
                )
                for value in values
            ]
        except Exception as error:
            raise SandboxError(
                "sandbox returned invalid artifact export metadata",
                code="sandbox_protocol_error",
            ) from error

    @staticmethod
    def _safe_relative_path(path: str) -> str:
        normalized = path.replace("\\", "/")
        parts = normalized.strip("/").split("/")
        if (
            not normalized
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or any(not _ID_RE.fullmatch(part) for part in parts)
        ):
            raise ValueError("sandbox file paths must be safe relative paths")
        return "/".join(parts)

    async def stop(self, session_id: str) -> None:
        sid = self._safe_id(session_id)
        try:
            await self._request("POST", f"/v1/sessions/{sid}/stop")
        except SandboxError as error:
            if error.status_code == 409:
                current = await self.status(session_id)
                if current.status not in {"stopped", "expired"}:
                    raise
            elif error.status_code != 404:
                raise

    async def delete_session(self, session_id: str) -> None:
        sid = self._safe_id(session_id)
        try:
            await self._request("DELETE", f"/v1/sessions/{sid}")
        except SandboxError as error:
            if error.status_code != 404:
                raise

    async def reconcile_ambiguous_execution(
        self, error: SandboxExecutionAmbiguous
    ) -> SessionInfo | None:
        """Read session state after an ambiguous POST; deliberately never reruns."""
        try:
            session = await self.status(error.session_id)
        except SandboxError:
            return None
        error.session_status = session
        return session
