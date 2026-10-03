from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.redaction import redact
from app.policy.decisions import POLICY_VERSION, PolicyDecision
from app.db.models import AuditEvent, Event, Run


class MissingRecord(LookupError):
    pass


def append_event(
    session: Session, run_id: str, event_type: str, payload: dict[str, Any]
) -> Event:
    sequence = session.scalar(
        update(Run)
        .where(Run.id == run_id)
        .values(event_sequence=Run.event_sequence + 1)
        .returning(Run.event_sequence)
    )
    if sequence is None:
        raise MissingRecord("run not found")
    event = Event(
        run_id=run_id, sequence=sequence, type=event_type, payload=redact(payload)
    )
    session.add(event)
    session.flush()
    return event


def audit(
    session: Session,
    *,
    action: str,
    decision: str,
    reason_code: str,
    run_id: str | None = None,
    tool_call_id: str | None = None,
    source_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    policy = PolicyDecision(
        action=action,
        outcome=(
            "clarify"
            if decision in {"clarify", "needs_input"}
            else (
                "reject"
                if decision
                in {"rejected", "failed", "error", "cancelled", "budget_exhausted"}
                else "allow"
            )
        ),
        reason_code=reason_code,
    )
    event = AuditEvent(
        action=action,
        decision=decision,
        reason_code=reason_code,
        run_id=run_id,
        tool_call_id=tool_call_id,
        source_id=source_id,
        details=redact(
            {
                **(details or {}),
                "policy_version": POLICY_VERSION,
                "policy": (details or {}).get("policy", policy.model_dump()),
            }
        ),
    )
    session.add(event)
    session.flush()
    return event


def events_after(session: Session, run_id: str, sequence: int) -> list[Event]:
    return list(
        session.scalars(
            select(Event)
            .where(Event.run_id == run_id, Event.sequence > sequence)
            .order_by(Event.sequence)
            .limit(100)
        )
    )
