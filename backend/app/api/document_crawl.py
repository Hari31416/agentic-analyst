"""Queue approved website imports; content follows the normal HTML ingestion path."""

import asyncio
import hashlib
import io
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Job, Source
from app.db.session import get_session
from app.ingestion.crawl import CrawlError, CrawlRequest, crawl, normalize_url

router = APIRouter(tags=["documents"])
Db = Annotated[Session, Depends(get_session)]


@router.post("/api/workspaces/{workspace_id}/crawl", status_code=202)
def queue_crawl(workspace_id: UUID, body: CrawlRequest, session: Db) -> dict[str, Any]:
    settings = get_settings()
    if not settings.crawl_enabled:
        raise HTTPException(409, "Website ingestion is disabled")
    from app.api.resource_lifecycle import lock_workspace

    lock_workspace(session, str(workspace_id))
    try:
        url = normalize_url(body.url)
        from urllib.parse import urlsplit

        if urlsplit(url).hostname not in settings.crawl_approved_hosts:
            raise CrawlError("Destination host is not approved")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    job = Job(
        kind="crawl_documents",
        workspace_id=str(workspace_id),
        payload={
            "workspace_id": str(workspace_id),
            "request": {**body.model_dump(), "url": url},
        },
        dedupe_key=f"crawl:{uuid4()}",
    )
    session.add(job)
    session.commit()
    return {"job_id": job.id, "state": job.state}


@router.get("/api/ingestion-jobs/{job_id}")
def ingestion_job(job_id: UUID, session: Db) -> dict[str, Any]:
    job = session.get(Job, str(job_id))
    if job is None or job.kind not in {
        "crawl_documents",
        "ingest_document",
        "index_document",
    }:
        raise HTTPException(404, "Ingestion job not found")
    return {
        "id": job.id,
        "state": job.state,
        "attempts": job.attempts,
        "result": job.result,
    }


def process_crawl(
    job_id: str,
    payload: dict[str, Any],
    settings: Settings,
    db: sessionmaker[Session],
    guard: Any,
) -> dict[str, Any]:
    from app.api.documents import upload_document

    events: list[dict[str, Any]] = []

    def progress(event: dict[str, Any]) -> None:
        events.append(event)
        with db() as session:
            guard(session)
            job = session.get(Job, job_id)
            if job is None:
                raise CrawlError("Crawl job is unavailable")
            job.result = {"pages": events[-200:]}
            session.commit()

    if not settings.crawl_enabled:
        raise CrawlError("Website ingestion is disabled")
    pages = crawl(
        CrawlRequest.model_validate(payload["request"]),
        settings.crawl_approved_hosts,
        progress=progress,
    )
    document_ids: list[str] = []
    for page in pages:
        with db() as session:
            guard(session)
            session.commit()
            filename = hashlib.sha256(page.url.encode()).hexdigest()[:20] + ".html"
            uploaded = asyncio.run(
                upload_document(
                    UUID(payload["workspace_id"]),
                    UploadFile(filename=filename, file=io.BytesIO(page.content)),
                    session,
                )
            )
            document_ids.append(uploaded["document"]["id"])
            source = session.get(Source, uploaded["source"]["id"])
            if source is not None:
                source.details = {
                    **source.details,
                    "crawl_urls": list(
                        dict.fromkeys(source.details.get("crawl_urls", []) + [page.url])
                    ),
                }
                session.commit()
        progress({"url": page.url, "state": "queued", "document_id": document_ids[-1]})
    return {
        "document_ids": document_ids,
        "pages": events,
        "partial": any(event["state"] == "failed" for event in events),
    }


@router.get("/api/ingestion-capabilities")
def ingestion_capabilities() -> dict[str, Any]:
    settings = get_settings()
    return {
        "formats": ["pdf", "docx", "txt", "md", "html", "pptx"],
        "chunk_strategies": ["structure", "recursive", "parent_child"],
        "ocr": {
            "enabled": settings.ocr_enabled,
            "languages": settings.ocr_languages,
            "profile": settings.ingestion_profile,
        },
        "crawl": {
            "enabled": settings.crawl_enabled,
            "approved_hosts": settings.crawl_approved_hosts,
        },
    }
