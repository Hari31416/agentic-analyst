"""Evaluation calls the same HTTP APIs as the workspace UI, without DB access."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, cast

import httpx

from evaluation.identity import fixture_path

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ORIGINAL_BYTES = 25 * 1024 * 1024
TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "budget_exhausted",
    "awaiting_clarification",
}


class ApiFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.code, self.status = code, status
        self.details = details or {}
        super().__init__(code)


def _safe_api_error(content: bytes, status: int) -> dict[str, Any]:
    """Keep only bounded diagnostic identifiers; never retain submitted values."""
    try:
        body = json.loads(content)
    except (ValueError, UnicodeError):
        return {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            return {"api_code": code}
    if status == 422 and isinstance(detail, list):
        fields = []
        for item in detail[:8]:
            if not isinstance(item, dict):
                continue
            location = item.get("loc")
            error_type = item.get("type")
            if isinstance(location, list):
                safe_location = [
                    str(part)[:40] for part in location if isinstance(part, (str, int))
                ]
                if safe_location:
                    fields.append(
                        {
                            "field": ".".join(safe_location)[:120],
                            "type": (
                                error_type[:80]
                                if isinstance(error_type, str)
                                and re.fullmatch(r"[a-zA-Z0-9_.-]+", error_type)
                                else "validation_error"
                            ),
                        }
                    )
        if fields:
            return {"validation_fields": fields}
    return {}


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

    async def login(self, username: str, password: str) -> None:
        result = await self.request(
            "POST", "/api/auth/login", json={"username": username, "password": password}
        )
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise ApiFailure("invalid_auth_response")
        self.http.headers["Authorization"] = "Bearer " + token
        self.http.cookies.clear()

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
                    diagnostic = bytearray()
                    async for chunk in response.aiter_bytes():
                        diagnostic.extend(chunk[: 4096 - len(diagnostic)])
                        if len(diagnostic) >= 4096:
                            break
                    raise ApiFailure(
                        "api_http_error",
                        response.status_code,
                        _safe_api_error(bytes(diagnostic), response.status_code),
                    )
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
        self,
        workspace_id: str,
        sources: list[dict[str, Any]],
        *,
        heartbeat: Any = None,
    ) -> list[dict[str, Any]]:
        if not any("document_id" in source for source in sources):
            return []
        last_heartbeat = time.monotonic()
        while True:
            documents = await self.request(
                "GET", f"/api/workspaces/{workspace_id}/documents"
            )
            wanted = {
                source["document_id"] for source in sources if "document_id" in source
            }
            selected = [doc for doc in documents if doc["id"] in wanted]
            failed = next(
                (
                    doc
                    for doc in selected
                    if doc.get("state") in {"failed", "ocr_needed"}
                ),
                None,
            )
            if failed is not None:
                details = failed.get("details") or {}
                error = details.get("error") if isinstance(details, dict) else {}
                code = error.get("code") if isinstance(error, dict) else None
                safe_code = (
                    code
                    if isinstance(code, str)
                    and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
                    else "source_processing_failed"
                )
                raise ApiFailure(
                    "source_processing_failed",
                    details={
                        "processing_code": safe_code,
                        "stage": str(failed.get("stage", "unknown"))[:40],
                        "state": str(failed.get("state", "failed"))[:40],
                    },
                )
            if len(selected) == len(wanted) and all(
                doc["state"] == "ready" for doc in selected
            ):
                return selected
            if heartbeat and time.monotonic() - last_heartbeat >= 15:
                heartbeat()
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.5)

    async def wait_run(self, run_id: str, *, heartbeat: Any = None) -> dict[str, Any]:
        last_heartbeat = time.monotonic()
        while True:
            run = await self.request("GET", f"/api/runs/{run_id}")
            if (
                run["state"] in TERMINAL
                and (run.get("outcome") or {}).get("cleanup") != "pending"
            ):
                return cast(dict[str, Any], run)
            if heartbeat and time.monotonic() - last_heartbeat >= 15:
                heartbeat()
                last_heartbeat = time.monotonic()
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
                    or sum(member.file_size for member in members)
                    > MAX_ARCHIVE_EXPANDED_BYTES
                ):
                    raise ApiFailure("archive_expansion_limit")
                names = [member.filename for member in members]
                if len(set(names)) != len(names):
                    raise ApiFailure("invalid_workspace_export")
                if archive.getinfo("manifest.json").file_size > MAX_RESPONSE_BYTES:
                    raise ApiFailure("archive_manifest_limit")
                manifest = json.loads(archive.read("manifest.json"))
                assets = {
                    (asset["owner_id"], asset["purpose"]): asset
                    for asset in manifest["assets"]
                }
                result = {}
                for source in sources:
                    asset = assets.get((source["id"], "original"))
                    actual = None
                    if asset:
                        member = archive.getinfo(asset["path"])
                        if member.file_size > MAX_ORIGINAL_BYTES:
                            raise ApiFailure("archive_original_limit")
                        hasher = hashlib.sha256()
                        used = 0
                        with archive.open(member) as original:
                            while chunk := original.read(1024 * 1024):
                                used += len(chunk)
                                if used > MAX_ORIGINAL_BYTES:
                                    raise ApiFailure("archive_original_limit")
                                hasher.update(chunk)
                        actual = hasher.hexdigest()
                    result[source["alias"]] = {
                        "before": source.get("content_hash"),
                        "after": actual,
                    }
                return result
        except (KeyError, ValueError, zipfile.BadZipFile) as error:
            raise ApiFailure("invalid_workspace_export") from error
