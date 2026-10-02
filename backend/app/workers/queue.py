from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Job, Run, now
from app.db.repository import append_event, audit


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    id: str
    kind: str
    token: str
    payload: dict[str, Any]
    run_id: str | None
    attempts: int


def claim(session: Session, owner: str, lease_seconds: int) -> Claim | None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        raise RuntimeError("durable workers require PostgreSQL locking")
    eligible = or_(
        and_(Job.state == "queued", Job.available_at <= func.now()),
        and_(Job.state == "running", Job.lease_expires_at <= func.now()),
    )
    # Exhausted jobs cannot remain invisibly running forever after the last worker dies.
    exhausted_runs = session.scalars(
        update(Job)
        .where(eligible, Job.attempts >= Job.max_attempts)
        .values(
            state="failed",
            lease_token=None,
            lease_owner=None,
            lease_expires_at=None,
            result={"error": "retry_limit_exceeded"},
        )
        .returning(Job.run_id)
    )
    for run_id in set(exhausted_runs):
        if run_id is None:
            continue
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.state in {
            "completed",
            "awaiting_clarification",
            "failed",
            "cancelled",
            "budget_exhausted",
        }:
            continue
        run.state = "failed"
        run.finished_at = now()
        has_session = bool(run.config.get("sandbox_session_id"))
        run.outcome = {
            "partial": True,
            "cleanup": "failed" if has_session else "complete",
            "cleanup_reason": (
                "sandbox_cleanup_unconfirmed; the external sandbox service TTL is expected to expire any orphaned session"
                if has_session
                else "no_sandbox_session_id_recorded; a session created before its ID was persisted may remain until the external service TTL expires"
            ),
            "error": {
                "code": "retry_limit_exceeded",
                "message": "Worker attempts were exhausted before the run completed.",
            },
        }
        append_event(session, run.id, "error", run.outcome["error"])
        append_event(session, run.id, "terminal", {"state": "failed"})
        audit(
            session,
            run_id=run.id,
            action="run.finalize",
            decision="failed",
            reason_code="retry_limit_exceeded",
        )
    job = session.scalar(
        select(Job)
        .where(eligible, Job.attempts < Job.max_attempts)
        .order_by(Job.available_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.state = "running"
    job.attempts += 1
    job.lease_owner = owner
    job.lease_token = str(uuid4())
    job.lease_expires_at = session.execute(select(func.now())).scalar_one() + timedelta(
        seconds=lease_seconds
    )
    session.flush()
    return Claim(
        job.id, job.kind, job.lease_token, job.payload, job.run_id, job.attempts
    )


def owned(job_id: str, token: str) -> Any:
    return and_(
        Job.id == job_id,
        Job.state == "running",
        Job.lease_token == token,
        Job.lease_expires_at > func.now(),
    )


def heartbeat(session: Session, job_id: str, token: str, lease_seconds: int) -> None:
    changed = session.scalar(
        update(Job)
        .where(owned(job_id, token))
        .values(lease_expires_at=func.now() + timedelta(seconds=lease_seconds))
        .returning(Job.id)
    )
    if changed is None:
        raise LeaseLost("job lease no longer belongs to this worker")


def finish(session: Session, job_id: str, token: str, result: dict[str, Any]) -> None:
    changed = session.scalar(
        update(Job)
        .where(owned(job_id, token))
        .values(
            state="completed",
            result=result,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
        .returning(Job.id)
    )
    if changed is None:
        raise LeaseLost("stale worker cannot finalize job")


def fail(session: Session, job_id: str, token: str, code: str, retryable: bool) -> None:
    job = session.scalar(select(Job).where(owned(job_id, token)).with_for_update())
    if job is None:
        raise LeaseLost("stale worker cannot fail job")
    job.state = "queued" if retryable and job.attempts < job.max_attempts else "failed"
    job.result = {"error": code}
    job.available_at = session.execute(select(func.now())).scalar_one() + timedelta(
        seconds=min(2**job.attempts, 60)
    )
    job.lease_token = None
    job.lease_owner = None
    job.lease_expires_at = None
