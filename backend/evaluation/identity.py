"""Stable, credential-free evaluation identities; cache reuse is explicit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.agent.loop import PROMPT_VERSION
from app.config import Settings
from app.policy.decisions import POLICY_VERSION
from app.sources.documents import EXTRACTOR_VERSION, CHUNKER_VERSION

ROOT = Path(__file__).resolve().parents[2]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def endpoint_identity(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Endpoint must be HTTP(S) without userinfo, query or fragment")
    return {
        "origin": f"{parsed.scheme}://{parsed.netloc}",
        "fingerprint": digest(url.rstrip("/")),
    }


def fixture_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Fixture path must name a file inside the fixture root")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Fixture exceeds the runner upload cap")
    return path


def build_identity(
    cases: list[Any],
    settings: Settings,
    api_url: str,
    fixture_root: Path,
    repeats: int,
    profile: str,
    timeout: float,
    concurrency: int = 1,
) -> dict[str, Any]:
    fixtures = {}
    for case in cases:
        for source in case.sources:
            if not source.path:
                raise ValueError("The first runner supports file-backed cases only")
            path = fixture_path(fixture_root, source.path)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if source.sha256 and actual != source.sha256:
                raise ValueError(f"Fixture hash mismatch for case {case.id}")
            fixtures[source.path] = actual
    code_hash = digest(
        {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in [ROOT / "backend/app", ROOT / "backend/evaluation"]
            for p in sorted(folder.rglob("*.py"))
        }
    )
    return {
        "schema_version": 1,
        "cases": [case.model_dump(mode="json") for case in cases],
        "fixtures": fixtures,
        "api": endpoint_identity(api_url),
        "model": settings.openai_model,
        "model_endpoint": (
            endpoint_identity(settings.openai_base_url)
            if settings.openai_base_url
            else None
        ),
        "prompt_version": PROMPT_VERSION,
        "policy_version": POLICY_VERSION,
        "document_extractor_version": EXTRACTOR_VERSION,
        "document_chunker_version": CHUNKER_VERSION,
        "retrieval_pipeline_version": "advanced-retrieval-v1",
        "application_and_runner_code_hash": code_hash,
        "dependency_lock_hash": hashlib.sha256(
            (ROOT / "backend/uv.lock").read_bytes()
        ).hexdigest(),
        "retrieval_profile": profile,
        "embedding": {
            "model": settings.embedding_model,
            "revision": settings.embedding_revision,
        },
        "reranker": {
            "model": settings.reranker_model,
            "revision": settings.reranker_revision,
        },
        "retrieval_aliases": settings.retrieval_aliases,
        "ingestion_profile": settings.ingestion_profile,
        "document_max_chunks": settings.document_max_chunks,
        "ocr": {"enabled": settings.ocr_enabled, "languages": settings.ocr_languages},
        "budgets": {
            "model_calls": settings.max_model_calls,
            "tool_calls": settings.max_tool_calls,
            "context_characters": settings.max_context_characters,
        },
        "sandbox_image": settings.sandbox_image,
        "judge": {"configured": False, "calibrated": False},
        "worker_concurrency": settings.worker_concurrency,
        "runner_concurrency": concurrency,
        "repeats": repeats,
        "timeout_seconds": timeout,
    }
