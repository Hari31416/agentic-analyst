"""Evaluation calls the same HTTP APIs as the workspace UI, without DB access."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, cast

import httpx

from evaluation.identity import fixture_path

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "budget_exhausted",
    "awaiting_clarification",
}


class ApiFailure(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(code)


class ApplicationClient:
    def __init__(
        self, api_url: str, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        # No environment proxy routing and no redirect-based endpoint changes.
        self.http = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        content = await self.bytes(method, path, **kwargs)
        try:
            return json.loads(content)
        except (ValueError, UnicodeError) as error:
            raise ApiFailure("invalid_api_json") from error

    async def bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        try:
            async with self.http.stream(method, path, **kwargs) as response:
                if response.status_code >= 300:
                    raise ApiFailure("api_http_error", response.status_code)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise ApiFailure("api_response_limit")
                return bytes(content)
        except httpx.HTTPError as error:
            raise ApiFailure("api_transport_failure") from error

    async def upload(
        self, workspace_id: str, source: Any, root: Path
    ) -> dict[str, Any]:
        path = fixture_path(root, source.path)
        document = source.kind not in {"csv", "xlsx", "json", "parquet"}
        endpoint = "documents" if document else "sources/files"
        data = await self.request(
            "POST",
            f"/api/workspaces/{workspace_id}/{endpoint}",
            files={"file": (source.name or path.name, path.read_bytes())},
        )
        uploaded = data["source"] if document else data
        uploaded["alias"] = source.alias
        if document:
            uploaded["document_id"] = data["document"]["id"]
        return cast(dict[str, Any], uploaded)

    async def wait_sources(
        self, workspace_id: str, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not any("document_id" in source for source in sources):
            return []
        while True:
            documents = await self.request(
                "GET", f"/api/workspaces/{workspace_id}/documents"
            )
            wanted = {
                source["document_id"] for source in sources if "document_id" in source
            }
            selected = [doc for doc in documents if doc["id"] in wanted]
            if any(doc["state"] == "failed" for doc in selected):
                raise ApiFailure("source_processing_failed")
            if len(selected) == len(wanted) and all(
                doc["state"] == "ready" for doc in selected
            ):
                return selected
            await asyncio.sleep(0.5)

    async def wait_run(self, run_id: str) -> dict[str, Any]:
        while True:
            run = await self.request("GET", f"/api/runs/{run_id}")
            if (
                run["state"] in TERMINAL
                and (run.get("outcome") or {}).get("cleanup") != "pending"
            ):
                return cast(dict[str, Any], run)
            await asyncio.sleep(0.5)

    async def original_hashes(
        self, workspace_id: str, sources: list[dict[str, Any]]
    ) -> dict[str, dict[str, str | None]]:
        content = await self.bytes("GET", f"/api/workspaces/{workspace_id}/export")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if (
                    len(members) > 10000
                    or sum(member.file_size for member in members) > MAX_RESPONSE_BYTES
                ):
                    raise ApiFailure("archive_expansion_limit")
                manifest = json.loads(archive.read("manifest.json"))
                assets = {
                    (asset["owner_id"], asset["purpose"]): asset
                    for asset in manifest["assets"]
                }
                result = {}
                for source in sources:
                    asset = assets.get((source["id"], "original"))
                    actual = (
                        hashlib.sha256(archive.read(asset["path"])).hexdigest()
                        if asset
                        else None
                    )
                    result[source["alias"]] = {
                        "before": source.get("content_hash"),
                        "after": actual,
                    }
                return result
        except (KeyError, ValueError, zipfile.BadZipFile) as error:
            raise ApiFailure("invalid_workspace_export") from error
