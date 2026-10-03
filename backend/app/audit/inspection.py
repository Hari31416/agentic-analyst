"""Bounded, redacted reconstruction of a run's retained audit trail."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.redaction import configured_secrets, redact
from app.db.models import (
    Artifact,
    AuditEvent,
    Evidence,
    Event,
    Message,
    Run,
    Source,
    ToolCall,
)

MAX_TEXT = 8_000
MAX_RESULT_CHARS = 24_000
MAX_ENTRY_BYTES = 48 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024
MAX_EXPORT_ITEMS = 500
MAX_INSPECTION_ITEMS = 1000


class _Budget:
    def __init__(self, byte_limit: int):
        self.remaining = byte_limit
        self.truncated = False
        self.truncated_strings = 0
        self.truncated_items = 0

    def take(self, text: str, *, max_chars: int) -> str:
        # Clip before redaction so oversized nested client payloads cannot make
        # regex scanning proportional to the original database field size.
        # Remove configured values first against the whole field, so a secret
        # crossing the output boundary cannot leak as a partial prefix.
        for secret in configured_secrets():
            text = text.replace(secret, "[redacted]")
        text = text[: min(max_chars, MAX_RESULT_CHARS) + 1]
        if "://" in text or "bearer " in text.casefold():
            cleaned = cast(str, redact(text))
        else:
            cleaned = text
        encoded = cleaned.encode("utf-8")
        allowed = min(max_chars, self.remaining)
        if len(encoded) > allowed:
            self.truncated = True
            self.truncated_strings += 1
            encoded = encoded[:allowed]
            cleaned = encoded.decode("utf-8", errors="ignore") + "…[truncated]"
            self.remaining = max(0, self.remaining - len(encoded))
            return cleaned
        self.remaining -= len(encoded)
        return cleaned


def _sanitize(
    value: Any, budget: _Budget, *, depth: int = 0, path: tuple[str, ...] = ()
) -> Any:
    if budget.remaining <= 0 or depth > 10:
        budget.truncated = True
        budget.truncated_items += 1
        return "[truncated]"
    if isinstance(value, str):
        return budget.take(value, max_chars=MAX_RESULT_CHARS)
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100 or budget.remaining <= 0:
                budget.truncated = True
                budget.truncated_items += len(value) - index
                break
            safe_key = budget.take(str(key), max_chars=256)
            result[safe_key] = (
                "[redacted]"
                if re_sensitive(str(key))
                else _sanitize(item, budget, depth=depth + 1, path=path + (str(key),))
            )
        return result
    if isinstance(value, (list, tuple)):
        list_result = []
        for index, item in enumerate(value):
            if index >= 100 or budget.remaining <= 0:
                budget.truncated = True
                budget.truncated_items += len(value) - index
                break
            list_result.append(_sanitize(item, budget, depth=depth + 1, path=path))
        return list_result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return budget.take(str(value), max_chars=MAX_RESULT_CHARS)


def re_sensitive(key: str) -> bool:
    # Keep the same field-name policy as the shared audit redactor.
    from app.audit.redaction import SENSITIVE

    return bool(SENSITIVE.search(key))


def _bounded(value: Any, *, chars: int = MAX_RESULT_CHARS) -> Any:
    return _sanitize(value, _Budget(min(MAX_ENTRY_BYTES, chars * 4)))


def _bounded_export(value: dict[str, Any]) -> tuple[dict[str, Any], _Budget]:
    budget = _Budget(MAX_EXPORT_BYTES - 128 * 1024)
    return _sanitize(value, budget), budget


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _entry(
    kind: str, row: Any, action: str, state: str, details: Any
) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": kind,
        "action": action,
        "state": state,
        "created_at": _iso(row.created_at),
        "details": _bounded(details),
    }


def collect_entries(
    session: Session, run: Run, *, action: str | None = None, state: str | None = None
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for event_row in session.scalars(
        select(Event)
        .where(Event.run_id == run.id)
        .order_by(Event.sequence)
        .limit(MAX_INSPECTION_ITEMS)
    ):
        entry = _entry(
            "event",
            event_row,
            event_row.type,
            str(event_row.payload.get("state", event_row.type)),
            event_row.payload,
        )
        entries.append(entry)
    for tool_row in session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id == run.id)
        .order_by(ToolCall.created_at, ToolCall.id)
        .limit(MAX_INSPECTION_ITEMS)
    ):
        details = {
            "tool_call_id": tool_row.id,
            "provider_call_id": tool_row.provider_call_id,
            "name": tool_row.name,
            "decision": tool_row.decision,
            "input_reference": tool_row.input_reference,
            "result": tool_row.result,
            "started_at": _iso(tool_row.started_at),
            "finished_at": _iso(tool_row.finished_at),
        }
        entries.append(
            _entry(
                "tool_call", tool_row, f"tool.{tool_row.name}", tool_row.status, details
            )
        )
    source_ids = run.selected_source_ids or []
    audit_query = (
        select(AuditEvent)
        .where(
            or_(
                AuditEvent.run_id == run.id,
                (AuditEvent.run_id.is_(None) & AuditEvent.source_id.in_(source_ids)),
            )
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .limit(MAX_INSPECTION_ITEMS)
    )
    for audit_row in session.scalars(audit_query):
        details = {
            "decision": audit_row.decision,
            "reason_code": audit_row.reason_code,
            "tool_call_id": audit_row.tool_call_id,
            "source_id": audit_row.source_id,
            "details": audit_row.details,
        }
        entries.append(
            _entry("audit", audit_row, audit_row.action, audit_row.decision, details)
        )
    if action:
        entries = [item for item in entries if item["action"] == action]
    if state:
        entries = [item for item in entries if item["state"] == state]
    entries.sort(key=lambda item: (item["created_at"] or "", item["id"], item["kind"]))
    return entries


def run_export(session: Session, run: Run) -> dict[str, Any]:
    source_ids = run.selected_source_ids or []
    source_audit_filter = or_(
        AuditEvent.run_id == run.id,
        (AuditEvent.run_id.is_(None) & AuditEvent.source_id.in_(source_ids)),
    )
    messages = session.scalars(
        select(Message)
        .where(Message.run_id == run.id)
        .order_by(Message.created_at, Message.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    sources = session.scalars(
        select(Source).where(Source.id.in_(run.selected_source_ids or []))
    ).all()
    tools = session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id == run.id)
        .order_by(ToolCall.created_at, ToolCall.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    evidences = session.scalars(
        select(Evidence)
        .where(Evidence.run_id == run.id)
        .order_by(Evidence.created_at, Evidence.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    artifacts = session.scalars(
        select(Artifact)
        .where(Artifact.run_id == run.id)
        .order_by(Artifact.created_at, Artifact.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    events = session.scalars(
        select(Event)
        .where(Event.run_id == run.id)
        .order_by(Event.sequence)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    audits = session.scalars(
        select(AuditEvent)
        .where(source_audit_filter)
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    collection_totals = {
        "messages": session.scalar(
            select(func.count()).select_from(Message).where(Message.run_id == run.id)
        )
        or 0,
        "tool_calls": session.scalar(
            select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run.id)
        )
        or 0,
        "evidence": session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.run_id == run.id)
        )
        or 0,
        "artifacts": session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.run_id == run.id)
        )
        or 0,
        "events": session.scalar(
            select(func.count()).select_from(Event).where(Event.run_id == run.id)
        )
        or 0,
        "audit_events": session.scalar(
            select(func.count()).select_from(AuditEvent).where(source_audit_filter)
        )
        or 0,
    }
    question = next((m.content for m in messages if m.role == "user"), None)
    document = {
        "schema_version": 1,
        "run": {
            "id": run.id,
            "state": run.state,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "outcome": _bounded(run.outcome),
            "config": _bounded(run.config),
        },
        "question": _bounded(question, chars=MAX_TEXT),
        "selected_sources": [
            {
                "id": s.id,
                "display_name": s.display_name,
                "kind": s.kind,
                "version": s.version,
                "state": s.state,
            }
            for s in sources
        ],
        "messages": [
            _bounded(
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "references": m.references,
                },
                chars=MAX_ENTRY_BYTES // 4,
            )
            for m in messages
        ],
        "tool_calls": [
            _bounded(
                {
                    "id": t.id,
                    "name": t.name,
                    "decision": t.decision,
                    "status": t.status,
                    "input_reference": t.input_reference,
                    "result": t.result,
                    "started_at": _iso(t.started_at),
                    "finished_at": _iso(t.finished_at),
                },
                chars=MAX_ENTRY_BYTES // 4,
            )
            for t in tools
        ],
        "evidence": [
            _bounded(
                {
                    "id": e.id,
                    "kind": e.kind,
                    "source_ids": e.source_ids,
                    "details": e.details,
                },
                chars=MAX_ENTRY_BYTES // 4,
            )
            for e in evidences
        ],
        "artifacts": [
            {
                "id": a.id,
                "display_name": a.display_name,
                "media_type": a.media_type,
                "byte_size": a.byte_size,
                "sha256": a.sha256,
                "lineage": a.lineage,
                "durable": a.durable,
            }
            for a in artifacts
        ],
        "events": [
            _bounded(
                {
                    "id": e.id,
                    "sequence": e.sequence,
                    "type": e.type,
                    "created_at": _iso(e.created_at),
                    "payload": e.payload,
                },
                chars=MAX_ENTRY_BYTES // 4,
            )
            for e in events
        ],
        "audit_events": [
            _bounded(
                {
                    "id": a.id,
                    "action": a.action,
                    "decision": a.decision,
                    "reason_code": a.reason_code,
                    "source_id": a.source_id,
                    "tool_call_id": a.tool_call_id,
                    "created_at": _iso(a.created_at),
                    "details": a.details,
                },
                chars=MAX_ENTRY_BYTES // 4,
            )
            for a in audits
        ],
        "limits": {},
    }
    bounded, budget = _bounded_export(document)
    truncated_counts = {
        name: max(0, total - MAX_EXPORT_ITEMS)
        for name, total in collection_totals.items()
        if total > MAX_EXPORT_ITEMS
    }
    bounded["limits"] = {
        "max_items_per_collection": MAX_EXPORT_ITEMS,
        "max_export_bytes": MAX_EXPORT_BYTES,
        "truncated_counts": truncated_counts,
        "truncated_content_fields": budget.truncated_strings,
        "truncated_nested_items": budget.truncated_items,
        "byte_budget_truncated": budget.truncated,
        "policy_unrecorded": not any(
            "policy_version" in (a.details or {}) for a in audits
        ),
    }
    # The sanitizer reserves room for this summary. Keep a hard serialized cap
    # as a final guard if structural overhead grows in a later schema revision.
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_EXPORT_BYTES:
        bounded["tool_calls"] = []
        bounded["evidence"] = []
        bounded["events"] = []
        bounded["audit_events"] = []
        bounded["limits"]["byte_budget_truncated"] = True
        bounded["limits"]["truncated_nested_items"] += 1
    return bounded
