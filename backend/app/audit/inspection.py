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
        self.byte_truncated = False
        self.nested_truncated = False
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
            self.truncated_strings += 1
            if self.remaining < max_chars:
                self.byte_truncated = True
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
        if budget.remaining <= 0:
            budget.byte_truncated = True
        if depth > 10:
            budget.nested_truncated = True
        budget.truncated_items += 1
        return "[truncated]"
    if isinstance(value, str):
        return budget.take(value, max_chars=MAX_RESULT_CHARS)
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100 or budget.remaining <= 0:
                if budget.remaining <= 0:
                    budget.byte_truncated = True
                if index >= 100:
                    budget.nested_truncated = True
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
            if index >= MAX_EXPORT_ITEMS or budget.remaining <= 0:
                if budget.remaining <= 0:
                    budget.byte_truncated = True
                if index >= MAX_EXPORT_ITEMS:
                    budget.nested_truncated = True
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


def _bounded_entry(
    value: Any, stats: dict[str, int], *, chars: int = MAX_RESULT_CHARS
) -> Any:
    budget = _Budget(min(MAX_ENTRY_BYTES, chars * 4))
    result = _sanitize(value, budget)
    stats["truncated_content_fields"] += budget.truncated_strings
    stats["truncated_nested_items"] += budget.truncated_items
    stats["entry_byte_budget_truncations"] += int(budget.byte_truncated)
    stats["entry_nested_limit_truncations"] += int(budget.nested_truncated)
    return result


def _bounded_export(value: dict[str, Any]) -> tuple[dict[str, Any], _Budget]:
    budget = _Budget(MAX_EXPORT_BYTES - 512 * 1024)
    return _sanitize(value, budget), budget


def _declared_ids(messages: list[Message], field: str) -> list[str]:
    ids: list[str] = []
    for message in messages:
        value = (message.references or {}).get(field, [])
        if isinstance(value, list):
            ids.extend(item for item in value if isinstance(item, str))
        if len(ids) >= MAX_EXPORT_ITEMS:
            break
    return list(dict.fromkeys(ids[:MAX_EXPORT_ITEMS]))


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
    messages = list(
        session.scalars(
            select(Message)
            .where(Message.run_id == run.id)
            .order_by(Message.created_at, Message.id)
            .limit(MAX_EXPORT_ITEMS)
        ).all()
    )
    answer_messages = list(
        session.scalars(
            select(Message)
            .where(Message.run_id == run.id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id)
            .limit(1)
        ).all()
    )
    if answer_messages and all(
        answer_messages[0].id != message.id for message in messages
    ):
        # Preserve the terminal answer, and therefore its evidence declarations,
        # when an unusually long message history reaches the collection cap.
        if len(messages) >= MAX_EXPORT_ITEMS:
            messages = messages[:-1] + answer_messages
        else:
            messages = list(messages) + answer_messages
        messages.sort(key=lambda message: (message.created_at, message.id))
    declared_messages = list(messages) + list(answer_messages)
    declared_evidence_ids = _declared_ids(declared_messages, "evidence_ids")
    declared_artifact_ids = _declared_ids(declared_messages, "artifact_ids")
    sources = session.scalars(
        select(Source)
        .where(Source.id.in_(run.selected_source_ids or []))
        .order_by(Source.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    tools = session.scalars(
        select(ToolCall)
        .where(ToolCall.run_id == run.id)
        .order_by(ToolCall.created_at, ToolCall.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    evidence_scope = (
        select(Evidence)
        .join(Run, Evidence.run_id == Run.id)
        .where(Run.thread_id == run.thread_id)
    )
    declared_evidence = session.scalars(
        evidence_scope.where(Evidence.id.in_(declared_evidence_ids))
        .order_by(Evidence.created_at, Evidence.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    current_evidence = session.scalars(
        evidence_scope.where(
            Evidence.run_id == run.id,
            Evidence.id.not_in(declared_evidence_ids),
        )
        .order_by(Evidence.created_at, Evidence.id)
        .limit(max(0, MAX_EXPORT_ITEMS - len(declared_evidence)))
    ).all()
    evidences = sorted(
        [*declared_evidence, *current_evidence],
        key=lambda item: (item.created_at, item.id),
    )
    artifact_scope = (
        select(Artifact)
        .join(Run, Artifact.run_id == Run.id)
        .where(Run.thread_id == run.thread_id)
    )
    declared_artifacts = session.scalars(
        artifact_scope.where(Artifact.id.in_(declared_artifact_ids))
        .order_by(Artifact.created_at, Artifact.id)
        .limit(MAX_EXPORT_ITEMS)
    ).all()
    current_artifacts = session.scalars(
        artifact_scope.where(
            Artifact.run_id == run.id,
            Artifact.id.not_in(declared_artifact_ids),
        )
        .order_by(Artifact.created_at, Artifact.id)
        .limit(max(0, MAX_EXPORT_ITEMS - len(declared_artifacts)))
    ).all()
    artifacts = sorted(
        [*declared_artifacts, *current_artifacts],
        key=lambda item: (item.created_at, item.id),
    )
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
        "selected_sources": session.scalar(
            select(func.count()).select_from(Source).where(Source.id.in_(source_ids))
        )
        or 0,
        "messages": session.scalar(
            select(func.count()).select_from(Message).where(Message.run_id == run.id)
        )
        or 0,
        "tool_calls": session.scalar(
            select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run.id)
        )
        or 0,
        "evidence": session.scalar(
            select(func.count())
            .select_from(Evidence)
            .join(Run, Evidence.run_id == Run.id)
            .where(
                Run.thread_id == run.thread_id,
                or_(Evidence.run_id == run.id, Evidence.id.in_(declared_evidence_ids)),
            )
        )
        or 0,
        "artifacts": session.scalar(
            select(func.count())
            .select_from(Artifact)
            .join(Run, Artifact.run_id == Run.id)
            .where(
                Run.thread_id == run.thread_id,
                or_(Artifact.run_id == run.id, Artifact.id.in_(declared_artifact_ids)),
            )
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
    selected_versions = run.config.get("source_versions", {})
    exported_evidence_ids = {item.id for item in evidences}
    exported_artifact_ids = {item.id for item in artifacts}
    entry_truncations = {
        "truncated_content_fields": 0,
        "truncated_nested_items": 0,
        "entry_byte_budget_truncations": 0,
        "entry_nested_limit_truncations": 0,
    }
    document = {
        "schema_version": 1,
        "run": {
            "id": run.id,
            "state": run.state,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "outcome": _bounded_entry(run.outcome, entry_truncations),
            "config": _bounded_entry(run.config, entry_truncations),
        },
        "question": _bounded_entry(question, entry_truncations, chars=MAX_TEXT),
        "selected_sources": [
            {
                "id": s.id,
                "display_name": s.display_name,
                "kind": s.kind,
                "version": s.version,
                "selected_version": selected_versions.get(s.id),
                "current_version": s.version,
                "state": s.state,
            }
            for s in sources
        ],
        "messages": [
            _bounded_entry(
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "references": m.references,
                },
                entry_truncations,
                chars=MAX_ENTRY_BYTES // 4,
            )
            for m in messages
        ],
        "evidence": [
            _bounded_entry(
                {
                    "id": e.id,
                    "producing_run_id": e.run_id,
                    "kind": e.kind,
                    "source_ids": e.source_ids,
                    "details": e.details,
                },
                entry_truncations,
                chars=MAX_ENTRY_BYTES // 4,
            )
            for e in evidences
        ],
        "artifacts": [
            {
                "id": a.id,
                "producing_run_id": a.run_id,
                "display_name": a.display_name,
                "media_type": a.media_type,
                "byte_size": a.byte_size,
                "sha256": a.sha256,
                "lineage": a.lineage,
                "durable": a.durable,
            }
            for a in artifacts
        ],
        "tool_calls": [
            _bounded_entry(
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
                entry_truncations,
                chars=MAX_ENTRY_BYTES // 4,
            )
            for t in tools
        ],
        "events": [
            _bounded_entry(
                {
                    "id": e.id,
                    "sequence": e.sequence,
                    "type": e.type,
                    "created_at": _iso(e.created_at),
                    "payload": e.payload,
                },
                entry_truncations,
                chars=MAX_ENTRY_BYTES // 4,
            )
            for e in events
        ],
        "audit_events": [
            _bounded_entry(
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
                entry_truncations,
                chars=MAX_ENTRY_BYTES // 4,
            )
            for a in audits
        ],
        "limits": {},
    }
    bounded, budget = _bounded_export(document)
    retained_counts = {
        name: len(bounded.get(name, [])) if isinstance(bounded.get(name), list) else 0
        for name in collection_totals
    }
    truncated_counts = {
        name: max(0, total - retained_counts.get(name, 0))
        for name, total in collection_totals.items()
        if total > retained_counts.get(name, 0)
    }
    bounded["limits"] = {
        "max_items_per_collection": MAX_EXPORT_ITEMS,
        "max_export_bytes": MAX_EXPORT_BYTES,
        "truncated_counts": truncated_counts,
        "truncated_content_fields": budget.truncated_strings
        + entry_truncations["truncated_content_fields"],
        "truncated_nested_items": budget.truncated_items
        + entry_truncations["truncated_nested_items"],
        "entry_byte_budget_truncations": entry_truncations[
            "entry_byte_budget_truncations"
        ],
        "entry_nested_limit_truncations": entry_truncations[
            "entry_nested_limit_truncations"
        ],
        "nested_limits_truncated": bool(
            budget.nested_truncated
            or entry_truncations["entry_nested_limit_truncations"]
        ),
        "byte_budget_truncated": budget.byte_truncated,
        "policy_unrecorded": not any(
            "policy_version" in (a.details or {}) for a in audits
        ),
        "unresolved_reference_counts": {
            "evidence": sum(
                identity not in exported_evidence_ids
                for identity in declared_evidence_ids
            ),
            "artifacts": sum(
                identity not in exported_artifact_ids
                for identity in declared_artifact_ids
            ),
        },
        "source_versions_unrecorded": any(
            identity not in selected_versions for identity in source_ids
        ),
    }
    # The sanitizer reserves room for this summary. Keep a hard serialized cap
    # as a final guard if structural overhead grows in a later schema revision.
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_EXPORT_BYTES:
        for collection in ("tool_calls", "events", "audit_events"):
            bounded["limits"]["truncated_counts"][collection] = collection_totals[
                collection
            ]
            bounded[collection] = []
        bounded["limits"]["byte_budget_truncated"] = True
        bounded["limits"]["truncated_nested_items"] += 1
        bounded["limits"]["nested_limits_truncated"] = True
    return bounded
