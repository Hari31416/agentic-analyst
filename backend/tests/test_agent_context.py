import pytest

from app.agent.context import ContextLimitExceeded, select_thread_context
from app.agent.loop import AgentLoop
from app.agent.protocol import ModelResponse
from app.config import Settings


def test_context_keeps_user_corrections_sources_and_all_durable_references():
    history = [
        {
            "role": "user",
            "content": "Correction: use INR, not USD. Assume the reporting month is April.",
            "selected_source_ids": ["source-1"],
            "selected_dataset_ids": ["dataset-1"],
            "source_versions": {"source-1": 4},
        },
        {
            "role": "assistant",
            "content": "The definition of eligible means a record with status active. "
            + ("Older explanation. " * 80),
            "references": {
                "evidence_ids": ["evidence-1"],
                "artifact_ids": ["artifact-expired-session"],
            },
        },
        {"role": "user", "content": "Now calculate the April total."},
    ]

    context = select_thread_context(history, max_characters=400)

    assert "Correction: use INR, not USD." in context.messages[0]["content"]
    assert "Assume the reporting month is April." in context.messages[0]["content"]
    assert '"selected_source_ids":["source-1"]' in context.messages[0]["content"]
    assert '"selected_dataset_ids":["dataset-1"]' in context.messages[0]["content"]
    assert '"source_versions":{"source-1":4}' in context.messages[0]["content"]
    assert "eligible means a record" in context.messages[1]["content"]
    assert '"evidence_ids":["evidence-1"]' in context.messages[1]["content"]
    assert (
        '"artifact_ids":["artifact-expired-session"]' in context.messages[1]["content"]
    )
    assert context.messages[-1]["content"] == "Now calculate the April total."
    assert sum(len(message["content"]) for message in context.messages) <= 400


def test_context_fails_explicitly_when_verbatim_user_content_cannot_fit():
    with pytest.raises(ContextLimitExceeded, match="User messages"):
        select_thread_context(
            [{"role": "user", "content": "Keep this correction intact"}],
            max_characters=5,
        )


def test_context_ignores_non_conversation_rows():
    assert (
        select_thread_context(
            [{"role": "tool", "content": "internal result"}], max_characters=100
        ).messages
        == []
    )


def test_older_definition_survives_newest_long_unrelated_answer():
    history = [
        {"role": "assistant", "content": "Eligible means status is active."},
        {
            "role": "assistant",
            "content": ("The latest result contains 42 rows and lengthy details " * 30)
            + ".",
        },
        {"role": "user", "content": "What counted as eligible?"},
    ]

    view = select_thread_context(history, max_characters=100)

    assert "Eligible means status is active." in view.messages[0]["content"]
    assert view.compacted
    assert view.dropped_assistant_characters > 0
    assert view.omitted_assistant_messages == 1


def test_context_fails_if_protected_definition_does_not_fit():
    with pytest.raises(ContextLimitExceeded, match="definitions"):
        select_thread_context(
            [
                {"role": "assistant", "content": "Eligible means status active."},
                {"role": "user", "content": "Follow up"},
            ],
            max_characters=20,
        )


async def test_agent_loop_sends_compacted_history_to_model():
    class Model:
        messages = None

        async def complete(self, messages, _tools):
            self.messages = messages
            return ModelResponse(
                content='{"text":"Done","artifact_ids":[],"evidence_ids":[]}',
                finish_reason="stop",
            )

    events = []

    async def emit(name, payload):
        events.append((name, payload))

    async def noop(*_args):
        return None

    model = Model()
    settings = Settings(_env_file=None, max_context_characters=5000)
    history = [
        {
            "role": "assistant",
            "content": "The definition means preserve this fact. "
            + ("This is unrelated recent prose. " * 400),
        },
        {
            "role": "user",
            "content": "Correction: count in INR.",
            "selected_source_ids": ["source-1"],
        },
    ]

    await AgentLoop(model, settings, [], emit, noop).run(history, "en-IN")

    assert "The definition means preserve this fact." in model.messages[1]["content"]
    assert '"selected_source_ids":["source-1"]' in model.messages[2]["content"]
    assert "Correction: count in INR." in model.messages[2]["content"]
    trace = next(payload for name, payload in events if name == "context_selected")
    assert trace["compacted"] is True
    assert trace["dropped_assistant_characters"] > 0
    assert trace["protected_fact_sentences"] == 1
