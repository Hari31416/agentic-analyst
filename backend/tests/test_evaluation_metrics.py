from evaluation.contracts import EvaluationCase
from evaluation.metrics import (
    character_error_rate,
    retrieval_metrics,
    score_case,
    word_error_rate,
)


def _case() -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "id": "mixed-hi",
            "question": "कुल राशि क्या है?",
            "language": "hi",
            "tags": ["mixed", "hindi"],
            "answerability": "answerable",
            "sources": [
                {
                    "alias": "ledger",
                    "name": "ledger.csv",
                    "kind": "csv",
                    "sha256": "a" * 64,
                }
            ],
            "expectations": {
                "calculations": [
                    {
                        "key": "total",
                        "value": "125.50",
                        "unit": "INR",
                        "tolerance": "0.01",
                        "column": "amount",
                    }
                ],
                "passages": [
                    {"source_alias": "policy", "contains": "देय राशि INR 125.50"}
                ],
                "artifacts": [
                    {"media_type": "application/vnd.test+json", "schema": {"rows": 1}}
                ],
                "allowed_actions": ["dataset.profile", "sql.query"],
            },
            "review": {"provenance": "synthetic"},
        }
    )


def test_case_schema_forbids_unknown_fields_and_keeps_unicode():
    case = _case()
    assert case.question.startswith("कुल")
    assert case.schema_version == 1


def test_deliberately_wrong_calculation_citation_artifact_and_action_fail():
    results = score_case(
        _case(),
        {
            "run_state": "completed",
            "answer_text": "125.50 INR",
            "calculations": {
                "total": {"value": "120", "unit": "INR", "column": "amount"}
            },
            "evidence": [{"source_alias": "policy", "text": "देय राशि INR 120"}],
            "artifacts": [
                {
                    "media_type": "application/vnd.test+json",
                    "schema": {"rows": 0},
                    "exists": True,
                }
            ],
            "tool_calls": [{"action": "sql.query"}, {"action": "source.delete"}],
            "source_hashes": {"ledger": {"before": "a" * 64, "after": "b" * 64}},
        },
    )
    assert {item.name: item.status for item in results}["calculation:total"] == "fail"
    assert {item.name: item.status for item in results}["passage:1"] == "fail"
    assert {item.name: item.status for item in results}["artifact:1"] == "fail"
    assert {item.name: item.status for item in results}["action_allowlist"] == "fail"
    assert {item.name: item.status for item in results}[
        "source_immutable:ledger"
    ] == "fail"


def test_matching_structured_results_and_original_hindi_pass():
    results = score_case(
        _case(),
        {
            "run_state": "completed",
            "calculations": {
                "total": {"value": "125.505", "unit": "inr", "column": "AMOUNT"}
            },
            "evidence": [
                {"source_alias": "policy", "text": "परिपत्र: देय राशि INR 125.50 तक"}
            ],
            "artifacts": [
                {
                    "media_type": "application/vnd.test+json",
                    "schema": {"rows": 1, "columns": 2},
                }
            ],
            "tool_calls": [{"action": "sql.query"}],
            "source_hashes": {"ledger": {"before": "a" * 64, "after": "a" * 64}},
        },
    )
    assert all(
        item.status == "pass" for item in results if item.name != "label_provenance"
    )


def test_model_failure_is_review_pending_not_a_pass():
    results = score_case(_case(), {"run_state": "model_error"})
    assert len(results) == 1
    assert results[0].status == "needs_review"


def test_audit_tool_rows_and_evidence_source_versions_are_scored():
    case_data = _case().model_dump()
    case_data["sources"].append(
        {"alias": "policy", "name": "policy.pdf", "kind": "pdf", "version": "3"}
    )
    case = EvaluationCase.model_validate(case_data)
    results = score_case(
        case,
        {
            "run_state": "completed",
            "source_aliases": {"ledger": "src-ledger", "policy": "src-policy"},
            "tool_calls": [
                {
                    "name": "run_sql",
                    "result": {
                        "data": {
                            "rows": [{"amount": "125.50"}],
                            "units": {"amount": "INR"},
                        }
                    },
                }
            ],
            "evidence": [
                {
                    "source_ids": ["src-policy"],
                    "details": {
                        "text": "देय राशि INR 125.50",
                        "source_versions": {"src-policy": "3"},
                    },
                }
            ],
        },
    )
    assert {item.name: item.status for item in results}["calculation:total"] == "pass"
    assert {item.name: item.status for item in results}["passage:1"] == "pass"

    wrong = score_case(
        case,
        {
            "run_state": "completed",
            "source_aliases": {"ledger": "src-ledger", "policy": "src-policy"},
            "tool_calls": [
                {
                    "name": "run_sql",
                    "result": {
                        "data": {
                            "rows": [{"amount": "125.50"}],
                            "units": {"amount": "INR"},
                        }
                    },
                }
            ],
            "evidence": [
                {
                    "source_ids": ["src-policy"],
                    "details": {
                        "text": "देय राशि INR 125.50",
                        "source_versions": {"src-policy": "2"},
                    },
                }
            ],
        },
    )
    assert {item.name: item.status for item in wrong}["passage:1"] == "fail"


def test_unicode_character_error_and_retrieval_metrics():
    assert character_error_rate("नमस्ते", "नमस्ते") == 0
    assert character_error_rate("नमस्ते", "नमस्त") > 0
    assert word_error_rate("hello दुनिया", "hello दुनिया") == 0
    assert retrieval_metrics({"p1", "p2"}, ["p3", "p2"], k=2) == {
        "precision": 0.5,
        "recall": 0.5,
        "reciprocal_rank": 0.5,
        "hit_rate": 1.0,
    }
