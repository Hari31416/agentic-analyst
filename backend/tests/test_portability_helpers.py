from uuid import uuid4

from app.portability.service import (
    _remap_citation_tokens,
    _remap_json,
    _safe_outcome,
    _safe_run_config,
)


def test_remaps_uuid_mapping_keys_and_values_but_preserves_expired_references():
    source_id = str(uuid4())
    evidence_id = str(uuid4())
    expired_id = str(uuid4())
    id_map = {source_id: str(uuid4()), evidence_id: str(uuid4())}

    remapped = _remap_json(
        {
            "source_versions": {source_id: 2},
            "schema_versions": {source_id: "schema-v1"},
            "expired": expired_id,
        },
        id_map,
    )

    assert remapped == {
        "source_versions": {id_map[source_id]: 2},
        "schema_versions": {id_map[source_id]: "schema-v1"},
        "expired": expired_id,
    }
    text = (
        f"[evidence:{evidence_id}] [evidence:{expired_id}] " f"literal UUID {source_id}"
    )
    assert _remap_citation_tokens(text, id_map) == (
        f"[evidence:{id_map[evidence_id]}] [evidence:{expired_id}] "
        f"literal UUID {source_id}"
    )


def test_run_config_and_outcome_use_safe_allowlists():
    source_id = str(uuid4())
    dataset_id = str(uuid4())
    config = _safe_run_config(
        {
            "sandbox_auth_token": "must-not-export",
            "provider": "secret-provider",
            "selected_dataset_ids": [dataset_id, "not-a-uuid"],
            "source_versions": {source_id: 3, "invalid": 1, dataset_id: True},
            "answer_language": "en-IN",
            "prompt_version": "analyst-v4",
            "retrieval_profile": "advanced",
            "profile": "not-the-public-field",
        }
    )
    assert config == {
        "selected_dataset_ids": [dataset_id],
        "source_versions": {source_id: 3},
        "answer_language": "en-IN",
        "prompt_version": "analyst-v4",
        "retrieval_profile": "advanced",
    }
    outcome = _safe_outcome(
        {
            "text": "result text",
            "clarification": "none",
            "provider_response": "secret",
        }
    )
    assert outcome == {"text": "result text", "clarification": "none"}
