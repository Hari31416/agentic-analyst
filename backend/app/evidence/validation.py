import re

from sqlalchemy.orm import Session

from app.contracts import FinalAnswer
from app.db.models import Artifact, Evidence, Run


def validate_answer(session: Session, run: Run, answer: FinalAnswer) -> None:
    # A prior artifact is valid only in this thread and with compatible selected-source lineage.
    allowed_sources = set(run.selected_source_ids)
    for artifact_id in answer.artifact_ids:
        artifact = session.get(Artifact, str(artifact_id))
        if artifact is None or not artifact.durable:
            raise ValueError("unknown artifact")
        producer = session.get(Run, artifact.run_id) if artifact.run_id else None
        if producer is None or producer.thread_id != run.thread_id:
            raise ValueError("artifact belongs to another thread")
        if any(
            run.config.get("source_versions", {}).get(identity) != version
            for identity, version in producer.config.get("source_versions", {}).items()
        ):
            raise ValueError("artifact comes from another selected source version")
        if not set(producer.selected_source_ids).issubset(allowed_sources):
            raise ValueError("artifact comes from unselected sources")
    for evidence_id in answer.evidence_ids:
        evidence = session.get(Evidence, str(evidence_id))
        if evidence is None:
            raise ValueError("unknown evidence")
        producer = session.get(Run, evidence.run_id)
        if (
            producer is None
            or producer.thread_id != run.thread_id
            or not set(evidence.source_ids).issubset(allowed_sources)
        ):
            raise ValueError(
                "evidence comes from an unselected source or another thread"
            )
        versions = evidence.details.get("source_versions", {})
        if any(
            run.config.get("source_versions", {}).get(identity) != version
            for identity, version in versions.items()
        ):
            raise ValueError("evidence comes from another selected source version")
    inline = re.findall(r"\[(evidence|artifact):([a-fA-F0-9-]{36})\]", answer.text)
    if any(
        identity
        not in {
            str(i)
            for i in (
                answer.evidence_ids if kind == "evidence" else answer.artifact_ids
            )
        }
        for kind, identity in inline
    ):
        raise ValueError("inline reference has no declared evidence")


def answer_checks(
    session: Session, run: Run, answer: FinalAnswer
) -> list[dict[str, str]]:
    """Descriptive checks, not proof that prose is supported or calculations correct."""
    from decimal import Decimal, InvalidOperation
    from sqlalchemy import select
    from app.db.models import ToolCall

    numbers: set[Decimal] = set()
    units: set[str] = set()
    partial = False

    def collect_rows(value: object) -> None:
        if isinstance(value, list):
            for row in value[:100]:
                if isinstance(row, dict):
                    for key, cell in row.items():
                        if re.search(r"id|date|year|code", str(key), re.I):
                            continue
                        try:
                            number = Decimal(str(cell).replace(",", ""))
                            if number.is_finite():
                                numbers.add(number)
                        except (InvalidOperation, ValueError):
                            pass

    for tool in session.scalars(
        select(ToolCall).where(ToolCall.run_id == run.id).limit(100)
    ):
        result = tool.result or {}
        if result.get("status") not in {"ok", "partial"}:
            continue
        partial = partial or result.get("status") == "partial"
        data = result.get("data", {})
        if not isinstance(data, dict):
            continue
        analysis = data.get("analysis", {})
        if not isinstance(analysis, dict):
            analysis = {}
        collect_rows(analysis.get("rows"))
        collect_rows(data.get("rows"))
        collect_rows(data.get("preview"))
        recorded_units = analysis.get("units", {})
        for unit in (
            recorded_units.values() if isinstance(recorded_units, dict) else []
        ):
            if isinstance(unit, str):
                units.add(unit)
    warnings = []
    if numbers:
        text = re.sub(r"`[^`]*`|[a-fA-F0-9]{8}-[a-fA-F0-9-]{27,}", "", answer.text)
        claims = re.findall(r"(?<![\w/-])\d[\d,]*(?:\.\d+)?(?![\w/-])", text)
        unsupported = []
        for claim in claims[:100]:
            try:
                value = Decimal(claim.replace(",", ""))
                if value not in numbers:
                    unsupported.append(claim)
            except InvalidOperation:
                pass
        if unsupported:
            warnings.append(
                {
                    "code": "numeric_support_unconfirmed",
                    "message": "Some answer numbers do not match the retained structured previews. Check source evidence and calculations; this check cannot prove a discrepancy.",
                }
            )
    if units and any(
        unit not in answer.text and not (unit == "INR" and "₹" in answer.text)
        for unit in units
    ):
        warnings.append(
            {
                "code": "units_missing",
                "message": "The answer omits units recorded with its calculations.",
            }
        )
    if partial and not re.search(
        r"partial|truncat|incomplete|सीमित|अधूर", answer.text, re.I
    ):
        warnings.append(
            {
                "code": "partial_result_unqualified",
                "message": "The answer uses partial tool results without noting the limitation.",
            }
        )
    has_artifacts = bool(answer.artifact_ids)
    if not has_artifacts and re.search(
        r"(?:report|file|chart|notebook).{0,24}(?:created|saved|generated)|(?:created|saved|generated).{0,24}(?:report|file|chart|notebook)",
        answer.text,
        re.I,
    ):
        warnings.append(
            {
                "code": "completion_claim_unconfirmed",
                "message": "A creation claim has no declared retained artifact.",
            }
        )
    return warnings[:12]
