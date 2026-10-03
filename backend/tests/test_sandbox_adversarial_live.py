"""Bounded adversarial probes against the configured real microVM only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from uuid import uuid4

import pytest

from app.sandbox.client import SandboxError
from app.storage.filesystem import FileStorage
from app.tools.python import PythonExecution
from tests.test_sandbox import _live_client


def _enabled_client():
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 to run real microVM probes")
    client = _live_client()
    if client is None:
        pytest.skip("sandbox URL and image must be configured")
    return client


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_microvm_denies_network_and_keeps_original_while_bounding_console(
    tmp_path,
):
    client = _enabled_client()
    storage = FileStorage(tmp_path / "storage")
    original = b"account,value\n00123,25000.00\n"
    original_key = "originals/source.csv"
    original_info = storage.put(original_key, original)
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    code = """import json, os, socket, sys
from pathlib import Path
Path('/workspace/inputs/source.csv').write_bytes(b'changed by guest')
checks = {}
for name, address in [('public', ('1.1.1.1', 80)), ('metadata', ('169.254.169.254', 80))]:
    try:
        socket.create_connection(address, timeout=0.75).close()
        checks[name] = 'connected'
    except OSError:
        checks[name] = 'blocked'
names = ('OPENAI_API_KEY', 'DATABASE_URL', 'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'SANDBOX_AUTH_TOKEN')
checks['configured_secret_names_present'] = [name for name in names if name in os.environ]
Path('checks.json').write_text(json.dumps(checks, sort_keys=True))
print('X' * 100000)
"""
    try:
        session = await execution._ensure_session()
        await client.write(session.id, "inputs/source.csv", original)
        result = await execution.execute(
            code, str(uuid4()), ["checks.json"], timeout_seconds=15
        )
        assert result.status == "ok", result.model_dump()
        assert result.data["stdout_truncated"] is True
        assert len(result.data["stdout"].encode()) <= 65_536 + len(
            "\n[output truncated]"
        )
        descriptor = next(
            item
            for item in result.data["artifacts"]
            if item["display_name"] == "checks.json"
        )
        checks = json.loads(storage.read(descriptor["storage_key"]))
        assert checks == {
            "configured_secret_names_present": [],
            "metadata": "blocked",
            "public": "blocked",
        }
        current = storage.read(original_key)
        assert current == original
        assert hashlib.sha256(current).hexdigest() == original_info.sha256
    finally:
        try:
            await execution.aclose()
        finally:
            await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_microvm_timeout_retains_marker_and_tears_down_session(tmp_path):
    client = _enabled_client()
    storage = FileStorage(tmp_path / "timeout-storage")
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    call_id = str(uuid4())
    session_id: str | None = None
    try:
        result = await execution.execute(
            "from pathlib import Path\nimport time\nPath('before.txt').write_bytes(b'kept-before-timeout')\ntime.sleep(45)",
            call_id,
            ["before.txt"],
            timeout_seconds=4,
        )
        assert execution.session is not None
        session_id = execution.session.id
        assert result.status == "partial", result.model_dump()
        marker = next(
            item
            for item in result.data["artifacts"]
            if item["display_name"] == "before.txt"
        )
        assert storage.read(marker["storage_key"]) == b"kept-before-timeout"
    finally:
        await execution.aclose()
        try:
            assert session_id is not None
            with pytest.raises(SandboxError) as missing:
                await client.status(session_id)
            assert missing.value.status_code == 404
        finally:
            await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_microvm_serializes_concurrent_calls_in_one_session(tmp_path):
    client = _enabled_client()
    storage = FileStorage(tmp_path / "serialized-storage")
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    first_call, second_call = str(uuid4()), str(uuid4())
    try:
        first, second = await asyncio.gather(
            execution.execute(
                "import time\nfrom pathlib import Path\ntime.sleep(0.5)\nPath('call.txt').write_text('first')",
                first_call,
                ["call.txt"],
                timeout_seconds=15,
            ),
            execution.execute(
                "from pathlib import Path\nPath('call.txt').write_text('second')",
                second_call,
                ["call.txt"],
                timeout_seconds=15,
            ),
        )
        assert first.status == second.status == "ok"
        assert execution.session is not None
        assert (
            first.data["session_id"]
            == second.data["session_id"]
            == execution.session.id
        )

        def output(result):
            artifact = next(
                item
                for item in result.data["artifacts"]
                if item["display_name"] == "call.txt"
            )
            return storage.read(artifact["storage_key"])

        assert output(first) == b"first"
        assert output(second) == b"second"
    finally:
        try:
            await execution.aclose()
        finally:
            await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_microvm_output_symlink_cannot_escape_call_directory(tmp_path):
    client = _enabled_client()
    storage = FileStorage(tmp_path / "symlink-storage")
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    try:
        result = await execution.execute(
            "from pathlib import Path\nPath('escape.py').symlink_to('/workspace/.agent/program.py')",
            str(uuid4()),
            ["escape.py"],
            timeout_seconds=15,
        )
        assert result.status == "partial", result.model_dump()
        assert result.data["artifacts"] == []
        assert set(result.data["collection_errors"]) & {
            "output_listing_failed",
            "output_read_failed",
            "unsafe_output_path",
            "requested_output_missing",
        }
        assert execution.session is not None
        session_id = execution.session.id
        await execution.aclose()

        # Verify the app's DELETE-and-reconcile fallback from a fresh connection.
        status_client = _enabled_client()
        try:
            with pytest.raises(SandboxError) as missing:
                await status_client.status(session_id)
            assert missing.value.status_code == 404
        finally:
            await status_client.aclose()
    finally:
        if not execution._closed:
            await execution.aclose()
        await client.aclose()


@pytest.mark.live
@pytest.mark.live_sandbox
@pytest.mark.asyncio
async def test_live_microvm_reports_resource_limit_visibility(tmp_path):
    client = _enabled_client()
    storage = FileStorage(tmp_path / "resource-storage")
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    code = """import json, os, resource, shutil
from pathlib import Path
def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None
mem_total = None
for line in Path('/proc/meminfo').read_text().splitlines():
    if line.startswith('MemTotal:'):
        mem_total = int(line.split()[1])
        break
stat = shutil.disk_usage('/workspace')
print(json.dumps({
    'cpu_count': os.cpu_count(),
    'memory_total_kib': mem_total,
    'cgroup_memory_max': read('/sys/fs/cgroup/memory.max'),
    'cgroup_cpu_max': read('/sys/fs/cgroup/cpu.max'),
    'process_address_space_limit': resource.getrlimit(resource.RLIMIT_AS),
    'workspace_disk_total_bytes': stat.total,
    'workspace_disk_free_bytes': stat.free,
}, sort_keys=True))
"""
    try:
        result = await execution.execute(code, str(uuid4()), timeout_seconds=15)
        assert result.status == "ok", result.model_dump()
        metrics = json.loads(result.data["stdout"])
        assert isinstance(metrics["cpu_count"], int)
        assert isinstance(metrics["workspace_disk_total_bytes"], int)
        assert metrics["workspace_disk_total_bytes"] > 0
        assert isinstance(metrics["memory_total_kib"], int)
        assert metrics["memory_total_kib"] > 0
        # These values are observational. The guest report does not prove host
        # enforcement or that its mounted workspace has a quota.
        print("RESOURCE_VISIBILITY=" + json.dumps(metrics, sort_keys=True))
    finally:
        try:
            await execution.aclose()
        finally:
            await client.aclose()
