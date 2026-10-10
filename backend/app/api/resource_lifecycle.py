"""Explicit resource deletion, provenance retention, and durable blob cleanup."""

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.contracts import CreateLabel, ThreadView, WorkspaceView
from app.db.models import (
    Artifact,
    AuditEvent,
    Connection,
    Dataset,
    Document,
    DocumentBlock,
    DocumentChunk,
    Evidence,
    Job,
    Run,
    Report,
    Source,
    SummaryCache,
    Thread,
    ToolCall,
    Workspace,
    now,
)
from app.db.session import get_session
from app.storage.keys import validate_key

router = APIRouter(tags=["resource lifecycle"])
Db = Annotated[Session, Depends(get_session)]


def lock_workspace(session: Session, workspace_id: str) -> Workspace:
    """Serialize resource creation/deletion on their common parent first."""
    workspace = session.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


def lock_thread(session: Session, thread_id: str) -> Thread:
    workspace_id = session.scalar(
        select(Thread.workspace_id).where(Thread.id == thread_id)
    )
    if workspace_id is None:
        raise HTTPException(404, "Thread not found")
    lock_workspace(session, workspace_id)
    thread = session.scalar(
        select(Thread)
        .where(Thread.id == thread_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if thread is None:
        raise HTTPException(404, "Thread not found")
    return thread


def lock_source_workspace(session: Session, source_id: str) -> Source:
    workspace_id = session.scalar(
        select(Source.workspace_id).where(Source.id == source_id)
    )
    if workspace_id is None:
        raise HTTPException(404, "Source not found")
    lock_workspace(session, workspace_id)
    source = session.get(Source, source_id, populate_existing=True)
    if source is None:
        raise HTTPException(404, "Source not found")
    return source


def references(value: Any, ids: set[str]) -> bool:
    """Recognize IDs and version/hash-qualified lineage in existing JSON contracts."""
    if isinstance(value, dict):
        return any(references(item, ids) for item in [*value.keys(), *value.values()])
    if isinstance(value, list):
        return any(references(item, ids) for item in value)
    if isinstance(value, str):
        prefix, separator, identity = value.split("@", 1)[0].partition(":")
        return value in ids or bool(
            separator
            and prefix in {"source", "dataset", "artifact", "document", "run"}
            and identity in ids
        )
    return False


def check_runs_idle(runs: list[Run]) -> None:
    if any(run.state in {"queued", "running"} for run in runs):
        raise HTTPException(
            409, "Cancel active runs and wait for cleanup before deleting"
        )
    if any(
        (run.outcome or {}).get("cleanup") == "pending"
        or (
            run.config.get("sandbox_session_id")
            and (run.outcome or {}).get("cleanup") != "complete"
        )
        for run in runs
    ):
        raise HTTPException(409, "Sandbox cleanup must complete before deleting")


def workspace_runs(session: Session, workspace_id: str) -> list[Run]:
    return list(
        session.scalars(
            select(Run)
            .join(Thread, Thread.id == Run.thread_id)
            .where(Thread.workspace_id == workspace_id)
        )
    )


def run_artifacts(session: Session, runs: list[Run]) -> list[Artifact]:
    ids = [run.id for run in runs]
    return list(
        session.scalars(
            select(Artifact).where(
                or_(
                    Artifact.run_id.in_(ids),
                    Artifact.tool_call_id.in_(
                        select(ToolCall.id).where(ToolCall.run_id.in_(ids))
                    ),
                )
            )
        )
    )


def queue_blob_deletion(session: Session, keys: set[str]) -> None:
    # These jobs have no parent FK: the outbox survives the deleting transaction.
    for key in sorted(keys):
        validate_key(key)
        session.add(
            Job(
                kind="delete_storage",
                dedupe_key=f"delete_storage:{uuid4()}",
                payload={"storage_key": key},
                max_attempts=10,
            )
        )


def document_image_keys(session: Session, document_ids: set[str]) -> set[str]:
    keys: set[str] = set()
    for model in (DocumentBlock, DocumentChunk):
        for location in session.scalars(
            select(model.location).where(model.document_id.in_(document_ids))
        ):
            key = (location or {}).get("image_key")
            if isinstance(key, str) and key:
                keys.add(key)
    return keys


def drop_summaries(session: Session, workspace_id: str, ids: set[str]) -> None:
    for summary in session.scalars(
        select(SummaryCache).where(SummaryCache.workspace_id == workspace_id)
    ):
        if references(summary.payload, ids):
            session.delete(summary)


@router.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceView)
def patch_workspace(workspace_id: UUID, body: CreateLabel, session: Db) -> Workspace:
    workspace = lock_workspace(session, str(workspace_id))
    workspace.label = body.label
    session.commit()
    return workspace


@router.patch("/api/threads/{thread_id}", response_model=ThreadView)
def patch_thread(thread_id: UUID, body: CreateLabel, session: Db) -> Thread:
    thread = lock_thread(session, str(thread_id))
    thread.label = body.label
    session.commit()
    return thread


@router.delete("/api/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: UUID, session: Db) -> Response:
    thread = lock_thread(session, str(thread_id))
    runs = list(session.scalars(select(Run).where(Run.thread_id == thread.id)))
    check_runs_idle(runs)
    run_ids = {run.id for run in runs}
    jobs = list(session.scalars(select(Job).where(Job.run_id.in_(run_ids))))
    artifacts = run_artifacts(session, runs)
    ids = run_ids | {artifact.id for artifact in artifacts}
    for job in session.scalars(select(Job).where(Job.run_id.is_(None))):
        if job.kind != "delete_storage" and references(job.payload, ids):
            jobs.append(job)
    if any(job.state in {"queued", "running"} for job in jobs):
        raise HTTPException(409, "Wait for thread jobs to finish before deleting")
    sources = list(
        session.scalars(
            select(Source).where(Source.workspace_id == thread.workspace_id)
        )
    )
    datasets = session.scalars(
        select(Dataset).where(Dataset.source_id.in_([src.id for src in sources]))
    )
    if any(references(source.details, ids) for source in sources) or any(
        references([dataset.lineage, dataset.details], ids) for dataset in datasets
    ):
        raise HTTPException(
            409,
            "Workspace datasets retain this thread's output provenance; delete the workspace to remove them together",
        )
    queue_blob_deletion(session, {artifact.storage_key for artifact in artifacts})
    for job in jobs:
        session.delete(job)
    session.delete(thread)
    session.commit()
    return Response(status_code=204)


@router.delete("/api/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: UUID, session: Db) -> Response:
    workspace = lock_workspace(session, str(workspace_id))
    runs = workspace_runs(session, workspace.id)
    check_runs_idle(runs)
    sources = list(
        session.scalars(select(Source).where(Source.workspace_id == workspace.id))
    )
    source_ids = {source.id for source in sources}
    doc_ids = set(
        session.scalars(select(Document.id).where(Document.source_id.in_(source_ids)))
    )
    # Also match historical JSON-only jobs and maintenance references.
    artifact_rows = run_artifacts(session, runs)
    resource_ids = (
        {run.id for run in runs} | source_ids | doc_ids | {a.id for a in artifact_rows}
    )
    jobs = list(
        session.scalars(
            select(Job).where(
                or_(
                    Job.workspace_id == workspace.id,
                    Job.run_id.in_([run.id for run in runs]),
                    Job.document_id.in_(doc_ids),
                )
            )
        )
    )
    for job in session.scalars(
        select(Job).where(Job.workspace_id.is_(None), Job.run_id.is_(None))
    ):
        if job.kind != "delete_storage" and references(
            job.payload, resource_ids | {workspace.id}
        ):
            if job not in jobs:
                jobs.append(job)
    if any(job.state in {"queued", "running"} for job in jobs):
        raise HTTPException(409, "Wait for workspace jobs to finish before deleting")
    datasets = list(
        session.scalars(select(Dataset).where(Dataset.source_id.in_(source_ids)))
    )
    keys = {a.storage_key for a in artifact_rows}
    keys.update(source.storage_key for source in sources if source.storage_key)
    keys.update(dataset.storage_key for dataset in datasets if dataset.storage_key)
    keys.update(document_image_keys(session, doc_ids))
    from app.api.reports import report_storage_keys

    report_ids = list(
        session.scalars(select(Report.id).where(Report.workspace_id == workspace.id))
    )
    keys.update(report_storage_keys(session, report_ids))
    queue_blob_deletion(session, keys)
    for job in jobs:
        session.delete(job)
    # Audits without a source/run FK can still contain workspace-scoped details.
    session.execute(
        delete(AuditEvent).where(
            AuditEvent.details["workspace_id"].as_string() == workspace.id
        )
    )
    session.delete(workspace)
    session.commit()
    return Response(status_code=204)


def remove_source(session: Session, source_id: str) -> dict[str, Any]:
    source = lock_source_workspace(session, source_id)
    document_ids = list(
        session.scalars(select(Document.id).where(Document.source_id == source.id))
    )
    # Lock jobs before refreshing extraction metadata, matching the worker order.
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                or_(
                    Job.document_id.in_(document_ids),
                    Job.payload["document_id"].as_string().in_(document_ids),
                )
            )
            .with_for_update()
        )
    )
    if any(job.state == "running" for job in jobs):
        raise HTTPException(409, "Wait for source processing to finish before deleting")
    session.refresh(source)
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.id.in_(document_ids))
            .execution_options(populate_existing=True)
        )
    )
    datasets = list(
        session.scalars(select(Dataset).where(Dataset.source_id == source.id))
    )
    ids = {source.id} | set(document_ids) | {dataset.id for dataset in datasets}
    runs = workspace_runs(session, source.workspace_id)
    affected_runs = [
        run for run in runs if references([run.selected_source_ids, run.config], ids)
    ]
    check_runs_idle(affected_runs)
    for job in jobs:
        if job.state == "queued":
            job.state = "cancelled"
    retained = bool(affected_runs)
    retained |= any(
        references([ev.source_ids, ev.details], ids)
        for ev in session.scalars(
            select(Evidence).where(Evidence.run_id.in_([run.id for run in runs]))
        )
    )
    other_sources = list(
        session.scalars(
            select(Source).where(
                Source.workspace_id == source.workspace_id, Source.id != source.id
            )
        )
    )
    retained |= any(references(other.details, ids) for other in other_sources)
    retained |= any(
        references([dataset.lineage, dataset.details], ids)
        for dataset in session.scalars(
            select(Dataset).where(
                Dataset.source_id.in_([other.id for other in other_sources])
            )
        )
    )
    retained |= any(
        references(artifact.lineage, ids) for artifact in run_artifacts(session, runs)
    )
    drop_summaries(session, source.workspace_id, ids)
    if retained:
        # Connection credentials have no citation value and must not be retained.
        session.execute(delete(Connection).where(Connection.source_id == source.id))
        source.state = "deleted"
        source.details = {
            **source.details,
            "removed_at": now().isoformat(),
            "retention": "archived_for_citations",
        }
        for document in documents:
            document.state = "deleted"
            document.stage = "removed"
            document.details = {
                **document.details,
                "removed_at": now().isoformat(),
                "retention": "archived_for_citations",
            }
        retention = "archived_for_citations"
    else:
        keys = {dataset.storage_key for dataset in datasets if dataset.storage_key}
        if source.storage_key:
            keys.add(source.storage_key)
        keys.update(document_image_keys(session, set(document_ids)))
        queue_blob_deletion(session, keys)
        for job in jobs:
            session.delete(job)
        session.delete(source)
        retention = "purged"
    session.commit()
    return {"source_id": source_id, "state": "deleted", "retention": retention}


@router.delete("/api/sources/{source_id}")
def delete_source(source_id: UUID, session: Db) -> dict[str, Any]:
    return remove_source(session, str(source_id))


@router.delete("/api/workspaces/{workspace_id}/sources/{source_id}")
def delete_workspace_source(
    workspace_id: UUID, source_id: UUID, session: Db
) -> dict[str, Any]:
    source = session.get(Source, str(source_id))
    if source is None or source.workspace_id != str(workspace_id):
        raise HTTPException(404, "Source not found")
    return remove_source(session, str(source_id))
