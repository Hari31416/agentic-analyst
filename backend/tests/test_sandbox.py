from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.sandbox.client import (
    SandboxExecutionAmbiguous,
    SandboxHTTPClient,
    SandboxError,
)
from app.config import get_settings
from app.storage.filesystem import FileStorage
from app.tools.python import PythonExecution, execute_python

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000002"
TOOL_CALL_ID = "00000000-0000-0000-0000-000000000003"


def _live_client() -> SandboxHTTPClient | None:
    settings = get_settings()
    base_url = os.getenv("LIVE_SANDBOX_URL") or settings.sandbox_base_url
    image = os.getenv("LIVE_SANDBOX_IMAGE") or settings.sandbox_image
    token = os.getenv("LIVE_SANDBOX_TOKEN")
    if token is None and settings.sandbox_auth_token:
        token = settings.sandbox_auth_token.get_secret_value()
    if not base_url or not image:
        return None
    return SandboxHTTPClient(
        base_url, image=image, auth_token=token, session_timeout_seconds=300
    )


def _session(status: str = "active") -> dict:
    return {
        "id": "session-1",
        "workspace_id": WORKSPACE_ID,
        "run_id": RUN_ID,
        "image": "analysis:dev",
        "status": status,
        "backend": "microsandbox",
        "root_path": "",
        "limits": {
            "cpu": 1,
            "memory_mb": 1024,
            "disk_mb": 2048,
            "timeout_seconds": 900,
            "network": "disabled",
            "allowed_hosts": [],
        },
        "metadata": {},
        "created_at": "2026-10-02T00:00:00Z",
        "expires_at": "2026-10-02T00:15:00Z",
        "last_heartbeat_at": None,
        "stopped_at": None,
    }


class FakeSandbox:
    def __init__(self, *, exec_timeout: bool = False, unsafe_listing: bool = False):
        self.requests: list[tuple[str, str, dict]] = []
        self.files: dict[str, bytes] = {}
        self.exec_timeout = exec_timeout
        self.unsafe_listing = unsafe_listing

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/v1/backends":
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "name": "microsandbox",
                            "available": True,
                            "supports_network_policy": True,
                        }
                    ]
                },
            )
        if path == "/v1/sessions" and request.method == "POST":
            assert body["backend"] == "microsandbox"
            return httpx.Response(201, json=_session())
        if path == "/v1/sessions/session-1" and request.method == "GET":
            return httpx.Response(200, json=_session())
        if path == "/v1/sessions/session-1/heartbeat":
            return httpx.Response(200, json=_session())
        if path == "/v1/sessions/session-1/execs" and request.method == "POST":
            if self.exec_timeout:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(
                200,
                json={
                    "id": "exec-1",
                    "session_id": "session-1",
                    "status": "completed",
                    "exit_code": 0,
                },
            )
        if path.endswith("/execs/exec-1/stdout"):
            return httpx.Response(200, content=b"stdout result\n")
        if path.endswith("/execs/exec-1/stderr"):
            return httpx.Response(200, content=b"stderr note\n")
        if path == "/v1/sessions/session-1/files" and request.method == "PUT":
            self.files[body["path"]] = base64.b64decode(body["content_base64"])
            return httpx.Response(
                200,
                json={
                    "path": body["path"],
                    "size_bytes": len(self.files[body["path"]]),
                    "sha256": hashlib.sha256(self.files[body["path"]]).hexdigest(),
                    "updated_at": "2026-10-02T00:00:00Z",
                },
            )
        if path == "/v1/sessions/session-1/files" and request.method == "GET":
            name = request.url.params["path"]
            if name not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[name])
        if path == "/v1/sessions/session-1/files/list":
            prefix = request.url.params.get("path", "")
            if self.unsafe_listing:
                entries = [
                    {
                        "path": f"{prefix}/../outside.txt",
                        "is_dir": False,
                        "size_bytes": 4,
                        "updated_at": "2026-10-02T00:00:00Z",
                    }
                ]
            else:
                content = b"id,total\n1,3\n"
                entries = [
                    {
                        "path": f"{prefix}/.keep",
                        "is_dir": False,
                        "size_bytes": 0,
                        "updated_at": "2026-10-02T00:00:00Z",
                    },
                    {
                        "path": f"{prefix}/result.csv",
                        "is_dir": False,
                        "size_bytes": len(content),
                        "updated_at": "2026-10-02T00:00:00Z",
                    },
                ]
                self.files[f"{prefix}/result.csv"] = content
            return httpx.Response(200, json=entries)
        if path == "/v1/sessions/session-1/artifacts/sync":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "remote-artifact-1",
                        "session_id": "session-1",
                        "source_path": body["paths"][0],
                        "artifact_uri": "sandbox-artifact://untrusted-location",
                        "size_bytes": 5,
                        "sha256": hashlib.sha256(b"value").hexdigest(),
                        "created_at": "2026-10-02T00:00:00Z",
                    }
                ],
            )
        if path == "/v1/sessions/session-1/stop":
            return httpx.Response(200, json=_session("stopped"))
        if path == "/v1/sessions/session-1" and request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})


def _client(handler: FakeSandbox) -> SandboxHTTPClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="http://sandbox.test", transport=transport)
    return SandboxHTTPClient(
        "http://sandbox.test",
        image="analysis:dev",
        auth_token="test-token",
        client=http,
    )


@pytest.mark.asyncio
async def test_sandbox_client_requests_microsandbox_with_network_disabled():
    handler = FakeSandbox()
    client = _client(handler)
    session = await client.create(WORKSPACE_ID, RUN_ID)
    assert session.backend == "microsandbox"
    assert session.network == "disabled"
    create = next(item for item in handler.requests if item[1] == "/v1/sessions")
    assert create[2]["limits"]["network"] == "disabled"
    assert create[2]["limits"]["allowed_hosts"] == []
    await client.aclose()


@pytest.mark.asyncio
async def test_sandbox_client_transfers_bounded_output_and_cleans_up():
    handler = FakeSandbox()
    client = _client(handler)
    await client.create(WORKSPACE_ID, RUN_ID)
    await client.write("session-1", "inputs/test.txt", b"source")
    assert await client.read("session-1", "inputs/test.txt", max_bytes=20) == b"source"
    with pytest.raises(SandboxError, match="file limit"):
        await client.read("session-1", "inputs/test.txt", max_bytes=3)
    execution = await client.execute(
        "session-1", "python -I /workspace/.agent/launcher.py", 30
    )
    assert execution.id == "exec-1"
    assert await client.stdout("session-1", execution.id, 7) == b"stdout r"
    assert await client.stderr("session-1", execution.id, 100) == b"stderr note\n"
    await client.heartbeat("session-1", 90)
    await client.stop("session-1")
    await client.delete_session("session-1")
    assert any(
        method == "DELETE" and path == "/v1/sessions/session-1"
        for method, path, _ in handler.requests
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_artifact_sync_is_metadata_only_and_rejects_unsafe_paths():
    handler = FakeSandbox()
    client = _client(handler)
    await client.create(WORKSPACE_ID, RUN_ID)
    with pytest.raises(ValueError, match="safe relative"):
        await client.sync_to_artifacts("session-1", ["../escape"], "run-1")
    artifacts = await client.sync_to_artifacts(
        "session-1", ["outputs/result.csv"], "run-1"
    )
    assert artifacts[0].artifact_uri.startswith("sandbox-artifact://")
    assert not any(
        method == "GET" and path.endswith("/artifacts/download")
        for method, path, _ in handler.requests
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_local_backend_or_missing_network_policy_is_rejected_before_create():
    class LocalOnly(FakeSandbox):
        async def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, {}))
            return httpx.Response(
                200,
                json={
                    "backends": [
                        {
                            "name": "local",
                            "available": True,
                            "supports_network_policy": False,
                        }
                    ]
                },
            )

    handler = LocalOnly()
    client = _client(handler)
    with pytest.raises(SandboxError, match="microsandbox"):
        await client.create(WORKSPACE_ID, RUN_ID)
    assert not any(path == "/v1/sessions" for _, path, _ in handler.requests)
    await client.aclose()


@pytest.mark.asyncio
async def test_lost_sync_exec_response_is_reconciled_and_never_retried():
    handler = FakeSandbox(exec_timeout=True)
    client = _client(handler)
    await client.create(WORKSPACE_ID, RUN_ID)
    with pytest.raises(SandboxExecutionAmbiguous) as caught:
        await client.execute("session-1", "python -I /workspace/.agent/launcher.py", 30)
    state = await client.reconcile_ambiguous_execution(caught.value)
    assert state is not None and caught.value.session_status == state
    exec_posts = [
        item
        for item in handler.requests
        if item[:2] == ("POST", "/v1/sessions/session-1/execs")
    ]
    assert len(exec_posts) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_python_tool_uses_fixed_launcher_and_verifies_durable_artifact(tmp_path):
    handler = FakeSandbox()
    client = _client(handler)
    storage = FileStorage(tmp_path / "storage")
    session = PythonExecution(client, storage, WORKSPACE_ID, RUN_ID)
    result = await session.execute(
        "print('hello')\nfrom pathlib import Path\nPath('result.csv').write_text('id,total\\n1,3\\n')",
        TOOL_CALL_ID,
        ["result.csv"],
        timeout_seconds=15,
    )
    assert result.status == "ok"
    assert result.data["stdout"] == "stdout result\n"
    assert result.data["stderr"] == "stderr note\n"
    assert result.data["artifacts"][0]["display_name"] == "result.csv"
    artifact = result.data["artifacts"][0]
    assert storage.read(artifact["storage_key"]) == b"id,total\n1,3\n"
    assert artifact["sha256"] == hashlib.sha256(b"id,total\n1,3\n").hexdigest()
    assert artifact["media_type"] == "text/csv"
    exec_call = next(
        item
        for item in handler.requests
        if item[:2] == ("POST", "/v1/sessions/session-1/execs")
    )
    assert exec_call[2]["command"] == "python -I /workspace/.agent/launcher.py"
    assert "print('hello')" not in exec_call[2]["command"]
    assert exec_call[2]["env"] == {}
    staged = handler.files[".agent/program.py"]
    assert staged.startswith(b"print('hello')")
    await session.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_python_tool_rejects_traversal_and_does_not_store_unsafe_listing(
    tmp_path,
):
    handler = FakeSandbox(unsafe_listing=True)
    client = _client(handler)
    execution = PythonExecution(client, FileStorage(tmp_path), WORKSPACE_ID, RUN_ID)
    with pytest.raises(ValueError, match="outputs"):
        await execution.execute("pass", TOOL_CALL_ID, ["../escape.txt"])
    result = await execution.execute("pass", TOOL_CALL_ID)
    assert result.status == "partial"
    assert result.data["artifacts"] == []
    assert result.data["collection_errors"] == ["unsafe_output_path"]
    await execution.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_python_call_salvages_outputs_before_stopping(tmp_path):
    class BlockingExec(FakeSandbox):
        def __init__(self):
            super().__init__()
            self.exec_started = asyncio.Event()

        async def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/sessions/session-1/execs":
                self.requests.append((request.method, request.url.path, {}))
                self.exec_started.set()
                await asyncio.Event().wait()
            return await super().__call__(request)

    handler = BlockingExec()
    client = _client(handler)
    storage = FileStorage(tmp_path / "cancel-storage")
    execution = PythonExecution(client, storage, WORKSPACE_ID, RUN_ID)
    task = asyncio.create_task(execution.execute("pass", TOOL_CALL_ID))
    await handler.exec_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert execution.cancelled_result is not None
    assert execution.cancelled_result.status == "partial"
    descriptor = execution.cancelled_result.data["artifacts"][0]
    assert storage.read(descriptor["storage_key"]) == b"id,total\n1,3\n"
    assert any(path.endswith("/stop") for _, path, _ in handler.requests)
    await execution.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_artifact_bytes_remain_after_session_cleanup(tmp_path):
    handler = FakeSandbox()
    client = _client(handler)
    storage = FileStorage(tmp_path / "durable")
    result = await execute_python(
        client,
        storage,
        WORKSPACE_ID,
        RUN_ID,
        TOOL_CALL_ID,
        "pass",
        ["result.csv"],
    )
    artifact = result.data["artifacts"][0]
    key = artifact["storage_key"]
    assert any(method == "DELETE" for method, _, _ in handler.requests)
    assert storage.read(key) == b"id,total\n1,3\n"
    await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_sandbox_runs_microvm_and_exports_file(tmp_path):
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 to run the real microVM probe")
    client = _live_client()
    if client is None:
        pytest.skip(
            "sandbox URL and image must be configured in Settings or LIVE_SANDBOX_*"
        )
    code = """import json, os, sys
from pathlib import Path
forbidden = ("OPENAI_API_KEY", "DATABASE_URL", "S3_SECRET_KEY", "SANDBOX_AUTH_TOKEN")
present = [name for name in forbidden if name in os.environ]
assert not present, f"sensitive environment leaked: {present}"
print("stdout-live-ok")
print("stderr-live-ok", file=sys.stderr)
Path("proof.txt").write_bytes(b"live-microvm-ok")
Path("environment.json").write_text(json.dumps({name: name in os.environ for name in forbidden}))
"""
    result = await execute_python(
        client,
        FileStorage(tmp_path / "live-storage"),
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        code,
        ["proof.txt", "environment.json"],
        timeout_seconds=30,
    )
    assert result.status == "ok", result.model_dump()
    assert result.data["stdout"] == "stdout-live-ok\n"
    assert result.data["stderr"] == "stderr-live-ok\n"
    proof_artifact = next(
        item for item in result.data["artifacts"] if item["display_name"] == "proof.txt"
    )
    stored = FileStorage(tmp_path / "live-storage").read(proof_artifact["storage_key"])
    assert stored == b"live-microvm-ok"
    environment_artifact = next(
        item
        for item in result.data["artifacts"]
        if item["display_name"] == "environment.json"
    )
    environment = json.loads(
        FileStorage(tmp_path / "live-storage").read(environment_artifact["storage_key"])
    )
    assert environment == {
        "OPENAI_API_KEY": False,
        "DATABASE_URL": False,
        "S3_SECRET_KEY": False,
        "SANDBOX_AUTH_TOKEN": False,
    }
    await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_sandbox_sessions_are_concurrent_and_isolated(tmp_path):
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 to run the real microVM probe")
    client = _live_client()
    if client is None:
        pytest.skip(
            "sandbox URL and image must be configured in Settings or LIVE_SANDBOX_*"
        )

    storage = FileStorage(tmp_path / "concurrent-storage")
    first = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    second = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    first_call_id = str(uuid4())
    second_call_id = str(uuid4())
    try:
        first_result, second_result = await asyncio.gather(
            first.execute(
                "import os\nfrom pathlib import Path\nos.environ['PY_SESSION_MARKER'] = 'first-only'\nPath('value.txt').write_text(os.environ['PY_SESSION_MARKER'])",
                first_call_id,
                ["value.txt"],
                timeout_seconds=30,
            ),
            second.execute(
                "import os\nfrom pathlib import Path\nassert 'PY_SESSION_MARKER' not in os.environ\nPath('value.txt').write_text('second-only')",
                second_call_id,
                ["value.txt"],
                timeout_seconds=30,
            ),
        )
        assert first_result.status == "ok", first_result.model_dump()
        assert second_result.status == "ok", second_result.model_dump()
        assert first.session is not None and second.session is not None
        assert first.session.id != second.session.id

        async def output(result):
            artifact = next(
                item
                for item in result.data["artifacts"]
                if item["display_name"] == "value.txt"
            )
            return storage.read(artifact["storage_key"])

        assert await output(first_result) == b"first-only"
        assert await output(second_result) == b"second-only"
    finally:
        await asyncio.gather(first.aclose(), second.aclose())
        await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_sandbox_cancellation_preserves_output_and_cleans_up(tmp_path):
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 to run the real microVM probe")
    client = _live_client()
    if client is None:
        pytest.skip(
            "sandbox URL and image must be configured in Settings or LIVE_SANDBOX_*"
        )

    storage = FileStorage(tmp_path / "cancel-storage")
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    call_id = str(uuid4())
    code = """import time
from pathlib import Path
Path("before-sleep.txt").write_bytes(b"saved-before-cancel")
time.sleep(90)
"""
    task = asyncio.create_task(
        execution.execute(code, call_id, ["before-sleep.txt"], timeout_seconds=180)
    )
    session_id: str | None = None
    try:
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            if task.done():
                await task
                pytest.fail("guest execution ended before cancellation probe")
            if execution.session is not None:
                session_id = execution.session.id
                entries = await client.list_files(session_id, f"outputs/{call_id}")
                if any(item.path.endswith("/before-sleep.txt") for item in entries):
                    break
            await asyncio.sleep(0.25)
        else:
            pytest.fail(
                "guest did not write the marker before the cancellation timeout"
            )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert execution.cancelled_result is not None
        assert execution.cancelled_result.status == "partial"
        artifact = next(
            item
            for item in execution.cancelled_result.data["artifacts"]
            if item["display_name"] == "before-sleep.txt"
        )
        assert storage.read(artifact["storage_key"]) == b"saved-before-cancel"

        assert session_id is not None
        stopped = await client.status(session_id)
        assert stopped.status in {"stopped", "expired"}
        await execution.aclose()
        with pytest.raises(SandboxError) as deleted:
            await client.status(session_id)
        assert deleted.value.status_code == 404
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await execution.aclose()
        await client.aclose()
