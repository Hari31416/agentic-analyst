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
    inline = re.findall(r"\[(?:evidence|artifact):([a-fA-F0-9-]{36})\]", answer.text)
    declared = {str(i) for i in [*answer.evidence_ids, *answer.artifact_ids]}
    if not set(inline).issubset(declared):
        raise ValueError("inline reference has no declared evidence")
