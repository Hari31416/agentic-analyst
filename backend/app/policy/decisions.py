"""App-owned decisions. Source/model text cannot change the capability set."""

from typing import Any, Literal
from pydantic import BaseModel, Field

from app.audit.redaction import contains_secret

POLICY_VERSION = "execution-policy-v1"
TOOLS = frozenset(
    {
        "run_python",
        "analyze_data",
        "generate_report",
        "summarize_documents",
        "search_documents",
        "source_passage",
        "list_sources",
        "dataset_profile",
        "inspect_schema",
        "sample_rows",
        "run_sql",
        "register_dataset",
        "inspect_artifact",
        "list_artifacts",
    }
)


class PolicyDecision(BaseModel):
    action: str = Field(min_length=1, max_length=120)
    outcome: Literal["allow", "reject", "clarify"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    policy_version: str = POLICY_VERSION


def tool_decision(
    name: str, arguments: dict[str, Any], sources: set[str], datasets: set[str]
) -> PolicyDecision:
    code = "validated_input"
    outcome: Literal["allow", "reject", "clarify"] = "allow"
    if name not in TOOLS:
        outcome, code = "reject", "unknown_tool"
    elif contains_secret(arguments):
        outcome, code = "reject", "configured_secret_detected"
    else:
        for field, allowed in [
            ("source_id", sources),
            ("dataset_id", datasets | sources),
            ("dataset_ids", datasets | sources),
            ("input_dataset_ids", datasets | sources),
        ]:
            values = arguments.get(field)
            if values is None:
                continue
            identities = values if isinstance(values, list) else [values]
            if any(str(item) not in allowed for item in identities):
                outcome, code = "reject", (
                    "source_not_selected"
                    if field == "source_id"
                    else "dataset_not_selected"
                )
                break
    return PolicyDecision(
        action=name[:120] or "unknown", outcome=outcome, reason_code=code
    )
