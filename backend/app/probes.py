"""Explicit live agent gate using synthetic inputs and the running API/worker.

Creates a retained evaluation workspace. Never prints credentials or model prompts.
Run with ``make live-agent`` after the model, sandbox, and Compose stack are ready.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4
from urllib.parse import urlsplit

import httpx

from app.contracts import TERMINAL_STATES
from app.config import get_settings
from app.db.models import Run, ToolCall
from app.db.session import factory
from app.sandbox.client import PINNED_SANDBOX_REVISION, SandboxError, SandboxHTTPClient
from sqlalchemy import select


async def request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> Any:
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


async def wait_terminal(client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        run = await request(client, "GET", f"/api/runs/{run_id}")
        if (
            run["state"] in TERMINAL_STATES
            and (run["outcome"] or {}).get("cleanup") != "pending"
        ):
            return cast(dict[str, Any], run)
        await asyncio.sleep(1)
    raise AssertionError(f"run {run_id} did not finish and confirm cleanup")


async def followup(client: httpx.AsyncClient, case: dict[str, Any]) -> dict[str, Any]:
    run = await request(
        client,
        "POST",
        f"/api/threads/{case['thread_id']}/runs",
        json={
            "text": f"Use the retained proof.csv artifact {case['artifact_id']} from the previous run. Stage it with input_artifact_ids and read /workspace/inputs/{case['artifact_id']} in run_python. Compute average grant amount as total_inr/count. Save average.csv with average_inr and one row. Use finish_answer with the new CSV artifact ID. Answer in English.",
            "answer_language": "en-IN",
        },
    )
    run = await wait_terminal(client, run["id"])
    assert run["state"] == "completed" and run["outcome"]["cleanup"] == "complete"
    outputs = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
    artifact = next(item for item in outputs if item["display_name"] == "average.csv")
    content = await client.get(f"/api/artifacts/{artifact['id']}/content")
    content.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(content.text)))
    assert len(rows) == 1 and float(rows[0]["average_inr"]) == 12500
    assert hashlib.sha256(content.content).hexdigest() == artifact["sha256"]
    assert artifact["id"] in run["outcome"]["artifact_ids"]
    return {
        "run_id": run["id"],
        "input_artifact_id": case["artifact_id"],
        "artifact_id": artifact["id"],
        "average_inr": 12500,
        "cleanup": "complete",
    }


def active_guest(run_id: str) -> tuple[str | None, str | None]:
    with factory()() as session:
        run = session.get(Run, run_id)
        assert run is not None
        tool_id = session.scalar(
            select(ToolCall.id).where(
                ToolCall.run_id == run_id,
                ToolCall.name == "run_python",
                ToolCall.status == "running",
            )
        )
        return run.config.get("sandbox_session_id"), tool_id


async def cancellation(client: httpx.AsyncClient, workspace_id: str) -> dict[str, Any]:
    thread = await request(
        client,
        "POST",
        f"/api/workspaces/{workspace_id}/threads",
        json={"label": "Live active cancellation"},
    )
    run = await request(
        client,
        "POST",
        f"/api/threads/{thread['id']}/runs",
        json={
            "text": "For this synthetic cancellation check call run_python once with code that writes partial.csv containing exactly stage\\nstarted\\n, prints started, and then time.sleep(60). Set output_paths to partial.csv and timeout_seconds to 120. The user will cancel during the sleep. Do not call finish_answer before the Python result.",
            "answer_language": "en-IN",
        },
    )
    settings = get_settings()
    sandbox = SandboxHTTPClient(
        settings.sandbox_base_url or "",
        image=settings.sandbox_image or "",
        auth_token=(
            settings.sandbox_auth_token.get_secret_value()
            if settings.sandbox_auth_token
            else None
        ),
    )
    session_id = None
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            session_id, tool_id = await asyncio.to_thread(active_guest, run["id"])
            if session_id and tool_id:
                try:
                    content = await sandbox.read(
                        session_id, f"outputs/{tool_id}/partial.csv", max_bytes=1024
                    )
                    if content == b"stage\nstarted\n":
                        break
                except SandboxError:
                    pass
            await asyncio.sleep(0.25)
        else:
            raise AssertionError(
                "guest did not write the partial output before cancellation"
            )
        await request(client, "POST", f"/api/runs/{run['id']}/cancel")
        run = await wait_terminal(client, run["id"])
        assert run["state"] == "cancelled" and run["outcome"]["cleanup"] == "complete"
        outputs = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
        partial = next(
            item for item in outputs if item["display_name"] == "partial.csv"
        )
        content_response = await client.get(f"/api/artifacts/{partial['id']}/content")
        content_response.raise_for_status()
        assert content_response.content == b"stage\nstarted\n"
        try:
            await sandbox.status(session_id)
        except SandboxError as error:
            assert error.status_code == 404
        else:
            raise AssertionError("cancelled guest session was not deleted")
        return {
            "run_id": run["id"],
            "state": "cancelled",
            "cleanup": "complete",
            "partial_artifact_id": partial["id"],
            "session_deleted": True,
        }
    finally:
        await sandbox.aclose()


async def evaluate(
    client: httpx.AsyncClient, workspace_id: str, language: str
) -> dict[str, Any]:
    thread = await request(
        client,
        "POST",
        f"/api/workspaces/{workspace_id}/threads",
        json={"label": f"Live {language} Python gate"},
    )
    prompt = (
        "Use run_python to add the two grant amounts INR 10000 and INR 15000. "
        "Save proof.csv with columns count,total_inr and one data row. Count the two grants. "
        "Print the total. Use finish_answer with the proof.csv artifact ID. Answer in English."
        if language == "en-IN"
        else "run_python से दो अनुदान राशियों INR 10000 और INR 15000 का जोड़ निकालें। "
        "proof.csv में count,total_inr कॉलम और एक डेटा पंक्ति सहेजें। दो अनुदानों की गिनती करें। "
        "कुल राशि प्रिंट करें। proof.csv के artifact ID के साथ finish_answer का उपयोग करें। उत्तर हिंदी में दें।"
    )
    body = {
        "request_id": str(uuid4()),
        "text": prompt,
        "answer_language": language,
        "selected_source_ids": [],
    }
    run = await request(client, "POST", f"/api/threads/{thread['id']}/runs", json=body)
    duplicate = await request(
        client, "POST", f"/api/threads/{thread['id']}/runs", json=body
    )
    assert duplicate["id"] == run["id"], "idempotent submission created another run"
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        run = await request(client, "GET", f"/api/runs/{run['id']}")
        if (
            run["state"] in TERMINAL_STATES
            and (run["outcome"] or {}).get("cleanup") != "pending"
        ):
            break
        await asyncio.sleep(1)
    assert (
        run["state"] == "completed"
    ), f"run {run['id']} ended {run['state']}: {(run['outcome'] or {}).get('error', {}).get('code')}"
    assert run["outcome"]["cleanup"] == "complete", "guest cleanup was not confirmed"
    answer = run["outcome"]["text"]
    assert "25" in answer, "answer omitted known total"
    if language == "hi-IN":
        assert any(
            "\u0900" <= char <= "\u097f" for char in answer
        ), "Hindi answer missing Devanagari"
    else:
        assert any("a" <= char.lower() <= "z" for char in answer)
        assert not any("\u0900" <= char <= "\u097f" for char in answer)
    outputs = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
    artifact = next(item for item in outputs if item["display_name"] == "proof.csv")
    assert (
        artifact["id"] in run["outcome"]["artifact_ids"]
    ), "answer omitted output reference"
    content = await client.get(f"/api/artifacts/{artifact['id']}/content")
    content.raise_for_status()
    assert hashlib.sha256(content.content).hexdigest() == artifact["sha256"]
    rows = list(csv.DictReader(io.StringIO(content.text)))
    assert (
        len(rows) == 1
        and int(rows[0]["count"]) == 2
        and float(rows[0]["total_inr"]) == 25000
    )
    events_response = await client.get(f"/api/runs/{run['id']}/events")
    events_response.raise_for_status()
    events = [
        json.loads(line[6:])
        for line in events_response.text.splitlines()
        if line.startswith("data: ")
    ]
    sequences = [event["sequence"] for event in events]
    assert sequences == list(
        range(1, len(events) + 1)
    ), "event replay has gaps or duplicates"
    assert any(
        event["type"] == "tool_finished"
        and event["payload"]["name"] == "run_python"
        and event["payload"]["status"] == "ok"
        for event in events
    )
    assert events[-1]["type"] == "terminal"
    cursor = sequences[len(sequences) // 2]
    replay = await client.get(
        f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(cursor)}
    )
    replay.raise_for_status()
    remaining = [
        json.loads(line[6:])["sequence"]
        for line in replay.text.splitlines()
        if line.startswith("data: ")
    ]
    assert remaining == [seq for seq in sequences if seq > cursor]
    history = await request(client, "GET", f"/api/threads/{thread['id']}/messages")
    assert len(history) == 2 and history[-1]["content"] == answer
    return {
        "language": language,
        "run_id": run["id"],
        "thread_id": thread["id"],
        "state": run["state"],
        "cleanup": run["outcome"]["cleanup"],
        "artifact_id": artifact["id"],
        "sha256": artifact["sha256"],
        "model_calls": run["outcome"]["model_calls"],
        "tool_calls": run["outcome"]["tool_calls"],
        "prompt_version": run["outcome"]["prompt_version"],
        "event_count": len(events),
        "known_count": 2,
        "known_total_inr": 25000,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--report", type=Path, default=Path("../data/reports/phase01-live.json")
    )
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=args.api_url, timeout=30) as client:
        readiness = await request(client, "GET", "/api/readiness")
        assert all(
            readiness["components"][key]["status"] in {"ready", "configured"}
            for key in ("database", "storage", "model", "sandbox")
        ), "configure required services first"
        workspace = await request(
            client,
            "POST",
            "/api/workspaces",
            json={"label": "Synthetic phase 01 live evaluation"},
        )
        cases = await asyncio.gather(
            *(
                evaluate(client, workspace["id"], language)
                for language in ("en-IN", "hi-IN")
            )
        )
        continued = await followup(client, cases[0])
        cancelled = await cancellation(client, workspace["id"])
    report = {
        "workspace_id": workspace["id"],
        "cases": cases,
        "followup": continued,
        "cancellation": cancelled,
    }
    settings = get_settings()
    report["configuration"] = {
        "model": settings.openai_model,
        "endpoint_host": urlsplit(settings.openai_base_url or "").hostname,
        "sandbox_revision": PINNED_SANDBOX_REVISION,
        "sandbox_image": settings.sandbox_image,
        "storage_backend": settings.storage_backend,
    }
    report["verified_at"] = datetime.now(UTC).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
