"""Lease-protected progress snapshots, independent of model output."""

from typing import Any

from sqlalchemy import select

from app.db.models import Job, ReportVersion
from app.db.session import factory
from app.workers.queue import Claim, LeaseLost, owned


def progress_view(version: ReportVersion) -> dict[str, Any]:
    progress = version.progress or {}
    state = version.state
    stage = progress.get("stage", "queued")
    message = progress.get("message", "Waiting for a worker")
    step = progress.get("step", 0)
    total = progress.get("total_steps", 5)
    if state == "ready":
        stage, message, step = "ready", "Report ready", total
    elif state == "failed":
        stage, message = "failed", "Report generation failed"
    return {
        "state": state,
        "stage": stage,
        "message": message,
        "step": step,
        "total_steps": total,
        # A terminal transition has a distinct ID even when no stage was written.
        "revision": int(progress.get("revision", 0)) * 3
        + (2 if state == "ready" else 1 if state == "failed" else 0),
    }


def update_progress(
    task: Claim,
    version_id: str,
    stage: str,
    message: str,
    step: int,
    total_steps: int = 5,
) -> None:
    with factory()() as session, session.begin():
        if (
            session.scalar(
                select(Job).where(owned(task.id, task.token)).with_for_update()
            )
            is None
        ):
            raise LeaseLost("report progress lease no longer belongs to this worker")
        version = session.scalar(
            select(ReportVersion)
            .where(ReportVersion.id == version_id)
            .with_for_update()
        )
        if version is None:
            raise LeaseLost("report version was removed")
        if version.state in {"ready", "failed"}:
            return
        version.progress = {
            "revision": int((version.progress or {}).get("revision", 0)) + 1,
            "stage": stage,
            "message": message,
            "step": step,
            "total_steps": total_steps,
        }
