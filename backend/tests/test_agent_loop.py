import json
from uuid import uuid4

import pytest

from app.agent.loop import AgentLoop, BudgetExhausted, Tool
from app.agent.protocol import ModelResponse, ModelToolCall
from app.agent.runtime import PythonInput
from app.config import Settings
from app.contracts import FinalAnswer, ToolResult


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append([*messages])
        return next(self.responses)


def response(name, args, call_id=None):
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ModelToolCall(
                id=call_id or str(uuid4()), name=name, arguments=json.dumps(args)
            )
        ],
    )


async def ignore(*_):
    pass


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


async def test_correlated_results_then_valid_final_answer():
    artifact_id = uuid4()
    model = ScriptedModel(
        [
            response("run_python", {"code": "print(42)"}, "call-one"),
            response(
                "finish_answer", {"text": "42", "artifact_ids": [str(artifact_id)]}
            ),
        ]
    )

    async def run_python(call, args):
        assert isinstance(args, PythonInput) and call.id == "call-one"
        return ToolResult(status="ok", summary="42", artifact_ids=[artifact_id])

    async def validate(answer):
        assert answer.artifact_ids == [artifact_id]

    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, run_python)],
        ignore,
        validate,
    )
    answer = await loop.run([{"role": "user", "content": "calculate"}], "hi-IN")
    assert answer.text == "42"
    assert model.requests[1][-1]["tool_call_id"] == "call-one"
    assert "hi-IN" in model.requests[0][0]["content"]


async def test_unknown_and_malformed_calls_never_execute_code():
    dispatched = []

    async def execute(*args):
        dispatched.append(args)
        return ToolResult(status="ok", summary="bad")

    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    model = ScriptedModel(
        [
            response("host_exec", {}),
            response("run_python", {"code": "print(42)", "host": "forbidden"}),
            response(
                "finish_answer", {"text": "Cannot execute", "clarification": True}
            ),
        ]
    )
    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, execute)],
        emit,
        ignore,
    )
    answer = await loop.run([], "en-IN")
    assert answer.clarification and not dispatched
    assert len([e for e in events if e[0] == "tool_rejected"]) == 2


async def test_invalid_references_can_be_repaired():
    model = ScriptedModel(
        [
            response("finish_answer", {"text": "bad", "artifact_ids": [str(uuid4())]}),
            response(
                "finish_answer",
                {"text": "No available evidence", "clarification": True},
            ),
        ]
    )

    async def validate(answer):
        if answer.artifact_ids:
            raise ValueError("unknown artifact")

    loop = AgentLoop(model, settings(), [], ignore, validate)
    assert (await loop.run([], "en-IN")).clarification
    assert (
        json.loads(model.requests[1][-1]["content"])["error"]["code"]
        == "invalid_answer"
    )


async def test_budget_and_duplicate_call_id_stop_dispatch():
    model = ScriptedModel(
        [response("unknown", {}, "same"), response("unknown", {}, "same")]
    )
    loop = AgentLoop(model, settings(), [], ignore, ignore)
    with pytest.raises(ValueError, match="reused"):
        await loop.run([], "en-IN")
    loop = AgentLoop(
        ScriptedModel([response("unknown", {})]),
        settings(max_model_calls=1),
        [],
        ignore,
        ignore,
    )
    with pytest.raises(BudgetExhausted, match="Model call limit"):
        await loop.run([], "en-IN")


async def test_truncated_model_response_is_incomplete():
    loop = AgentLoop(
        ScriptedModel([ModelResponse(content="unfinished", finish_reason="length")]),
        settings(),
        [],
        ignore,
        ignore,
    )
    with pytest.raises(BudgetExhausted):
        await loop.run([], "en-IN")


async def test_final_object_in_content_preserves_validated_references():
    artifact_id = uuid4()
    model = ScriptedModel(
        [
            ModelResponse(
                content=json.dumps(
                    {"text": "Saved result", "artifact_ids": [str(artifact_id)]}
                ),
                finish_reason="stop",
            )
        ]
    )

    async def validate(answer):
        assert answer.artifact_ids == [artifact_id]

    answer = await AgentLoop(model, settings(), [], ignore, validate).run([], "en-IN")
    assert answer.text == "Saved result" and answer.artifact_ids == [artifact_id]


async def test_prose_after_artifact_result_requires_referenced_final_answer():
    artifact_id = uuid4()
    model = ScriptedModel(
        [
            response("run_python", {"code": "print(42)"}),
            ModelResponse(content="42, saved a file", finish_reason="stop"),
            response(
                "finish_answer", {"text": "42", "artifact_ids": [str(artifact_id)]}
            ),
        ]
    )

    async def execute(*_):
        return ToolResult(status="ok", summary="42", artifact_ids=[artifact_id])

    answer = await AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, execute)],
        ignore,
        ignore,
    ).run([], "en-IN")
    assert answer.artifact_ids == [artifact_id] and len(model.requests) == 3


async def test_retryable_provider_failure_does_not_repeat_tools():
    from app.agent.model import ModelError

    class InterruptedModel(ScriptedModel):
        async def complete(self, messages, tools):
            item = await super().complete(messages, tools)
            if isinstance(item, Exception):
                raise item
            return item

    calls = []

    async def execute(*_):
        calls.append(1)
        return ToolResult(status="ok", summary="2")

    model = InterruptedModel(
        [
            response("run_python", {"code": "print(2)"}),
            ModelError("model_provider_error", "HTTP 503", retryable=True),
            response("finish_answer", {"text": "2"}),
        ]
    )
    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, execute)],
        ignore,
        ignore,
    )
    answer = await loop.run([], "en-IN")
    assert answer.text == "2" and calls == [1] and loop.model_calls == 3


async def test_validation_feedback_names_fields_without_echoing_input():
    dispatched = []

    async def execute(*args):
        dispatched.append(args)
        return ToolResult(status="ok", summary="unexpected")

    model = ScriptedModel(
        [
            response(
                "run_python",
                {"code": "print(1)", "input_artifact_ids": ["private-input-sentinel"]},
            ),
            response(
                "finish_answer",
                {"text": "Please select a valid artifact", "clarification": True},
            ),
        ]
    )
    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, execute)],
        ignore,
        ignore,
    )
    assert (await loop.run([], "en-IN")).clarification
    content = model.requests[1][-1]["content"]
    result = json.loads(content)
    assert result["data"]["validation_errors"][0]["field"] == "input_artifact_ids.0"
    assert "private-input-sentinel" not in content
    assert not dispatched


@pytest.mark.parametrize("content_answer", [False, True])
async def test_answer_rejections_record_reason_and_return_repair_feedback(
    content_answer,
):
    events = []
    invalid = {"text": "bad", "artifact_ids": [str(uuid4())]}
    first = (
        ModelResponse(content=json.dumps(invalid), finish_reason="stop")
        if content_answer
        else response("finish_answer", invalid)
    )
    model = ScriptedModel([first, response("finish_answer", {"text": "repaired"})])

    async def validate(answer):
        if answer.artifact_ids:
            raise ValueError("unknown artifact")

    async def emit(kind, payload):
        events.append((kind, payload))

    answer = await AgentLoop(model, settings(), [], emit, validate).run([], "en-IN")
    assert answer.text == "repaired"
    rejected = [
        payload
        for kind, payload in events
        if kind in {"answer_rejected", "tool_rejected"}
    ]
    assert rejected[0]["validation_errors"] == [
        {
            "field": "answer",
            "message": "unknown artifact",
            "type": "reference_validation",
        }
    ]
    assert "unknown artifact" in model.requests[1][-1]["content"]


async def test_final_schema_diagnostics_omit_inputs_and_redact_secrets(monkeypatch):
    import app.audit.redaction as redaction

    monkeypatch.setattr(
        redaction, "configured_secrets", lambda: ("private-test-value",)
    )
    events = []
    model = ScriptedModel(
        [
            response(
                "finish_answer",
                {"text": "private-test-value", "artifact_ids": ["private-test-value"]},
            ),
            response("finish_answer", {"text": "repaired"}),
        ]
    )

    async def emit(kind, payload):
        events.append((kind, payload))

    await AgentLoop(model, settings(), [], emit, ignore).run([], "en-IN")
    details = next(
        payload["validation_errors"]
        for kind, payload in events
        if kind == "tool_rejected"
    )
    assert details[0]["field"] == "artifact_ids.0"
    assert details[0]["type"] == "uuid_parsing"
    assert "private-test-value" not in json.dumps(details)
    assert set(details[0]) == {"field", "type", "message"}


def test_answer_diagnostics_redact_before_truncation(monkeypatch):
    import app.audit.redaction as redaction
    from app.agent.loop import answer_validation_errors

    monkeypatch.setattr(
        redaction, "configured_secrets", lambda: ("private-test-value",)
    )
    errors = answer_validation_errors(ValueError("x" * 290 + "private-test-value"))
    assert "private" not in errors[0]["message"]
    assert "[redacted]" in errors[0]["message"]
    assert len(errors[0]["message"]) == 300


async def test_sql_stderr_reaches_model_before_corrected_query():
    from app.contracts import SafeError
    from app.tools.structured import SQLInput

    stderr = (
        "Binder Error: Cannot compare values of type VARCHAR and type DECIMAL(2,1) "
        "- an explicit cast is required"
    )
    bad_sql = 'SELECT * FROM data_test WHERE "Buildings operational" < 1.0'
    fixed_sql = (
        "SELECT * FROM data_test WHERE "
        'TRY_CAST("Buildings operational" AS DOUBLE) < 1.0'
    )
    model = ScriptedModel(
        [
            response("run_sql", {"sql": bad_sql}, "bad-sql"),
            response("run_sql", {"sql": fixed_sql}, "fixed-sql"),
            response("finish_answer", {"text": "Query completed"}),
        ]
    )
    queries = []

    async def execute(call, args):
        queries.append(args.sql)
        if call.id == "bad-sql":
            return ToolResult(
                status="failed",
                summary="SQL execution failed",
                error=SafeError(
                    code="sandbox_output_collection_partial", message="Missing output"
                ),
                data={
                    "stderr": stderr,
                    "execution": {"status": "failed", "exit_code": 1},
                },
            )
        return ToolResult(status="ok", summary="Query returned rows")

    loop = AgentLoop(
        model, settings(), [Tool("run_sql", "SQL", SQLInput, execute)], ignore, ignore
    )
    await loop.run([{"role": "user", "content": "Find planned buildings"}], "en-IN")
    feedback = model.requests[1][-1]
    assert feedback["role"] == "tool"
    assert feedback["tool_call_id"] == "bad-sql"
    result = json.loads(feedback["content"])
    assert result["data"]["stderr"] == stderr
    assert result["data"]["execution"]["exit_code"] == 1
    assert queries == [bad_sql, fixed_sql]


async def test_invalid_tool_diagnostics_exclude_submitted_values():
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    model = ScriptedModel(
        [
            response("run_python", {"code": "print(42)", "extra": "private-value"}),
            response(
                "finish_answer", {"text": "Cannot execute", "clarification": True}
            ),
        ]
    )
    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, ignore)],
        emit,
        ignore,
    )
    await loop.run([{"role": "user", "content": "calculate"}], "en-IN")
    diagnostic = next(p for k, p in events if k == "tool_validation_diagnostic")
    assert diagnostic["model_calls"] == 1
    assert diagnostic["name"] == "run_python"
    assert diagnostic["validation_errors"][0]["field"] == "extra"
    assert diagnostic["error_message_characters"] > 0
    assert "private-value" not in json.dumps(events)
    response_info = next(p for k, p in events if k == "model_response_diagnostic")
    assert response_info["tool_calls"][0]["argument_characters"] > 0
    assert "arguments" not in response_info["tool_calls"][0]


async def test_oversized_validation_diagnostic_rejects_and_recovers():
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    model = ScriptedModel(
        [
            response(
                "run_python",
                {
                    "code": "print(42)",
                    **{f"unsupported_field_{n}": "unused" for n in range(10)},
                },
            ),
            response("finish_answer", {"text": "Recovered", "clarification": True}),
        ]
    )
    loop = AgentLoop(
        model,
        settings(),
        [Tool("run_python", "Python", PythonInput, ignore)],
        emit,
        ignore,
    )
    answer = await loop.run([{"role": "user", "content": "calculate"}], "en-IN")
    assert answer.text == "Recovered"
    diagnostic = next(p for k, p in events if k == "tool_validation_diagnostic")
    assert diagnostic["error_message_characters"] == 646
    result = json.loads(model.requests[1][-1]["content"])
    assert result["status"] == "rejected"
    assert len(result["error"]["message"]) == 500
    assert len(result["data"]["validation_errors"]) == 10


async def test_model_diagnostics_keep_duration_usage_without_content():
    events = []

    async def collect(kind, payload):
        events.append((kind, payload))

    model = ScriptedModel(
        [
            ModelResponse(
                content="private-content",
                finish_reason="stop",
                usage={
                    "total_tokens": 12,
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "provider_secret": 99,
                },
            )
        ]
    )
    await AgentLoop(model, settings(), [], collect, ignore).run([], "en-IN")
    diagnostic = next(
        payload for kind, payload in events if kind == "model_response_diagnostic"
    )
    assert diagnostic["duration_ms"] >= 0
    assert diagnostic["usage"] == {
        "total_tokens": 12,
        "prompt_tokens": 8,
        "completion_tokens": 4,
    }
    assert "private-content" not in str(diagnostic)


async def test_fatal_provider_error_records_model_attempt_duration():
    from app.agent.model import ModelError

    events = []

    async def collect(kind, payload):
        events.append((kind, payload))

    class Failing:
        async def complete(self, *_):
            raise ModelError("model_provider_error", "private-provider-body")

    with pytest.raises(ModelError):
        await AgentLoop(Failing(), settings(), [], collect, ignore).run([], "en-IN")
    failure = next(
        payload for kind, payload in events if kind == "model_request_failed"
    )
    assert failure["code"] == "model_provider_error"
    assert failure["model_calls"] == 1 and failure["duration_ms"] >= 0
    assert not failure["retryable"] and "private-provider-body" not in str(failure)


async def test_vision_parts_extracted_and_passed_for_sparse_image_passages(
    tmp_path, monkeypatch
):
    import io
    from PIL import Image
    from pydantic import BaseModel
    from app.storage.filesystem import FileStorage

    storage_root = tmp_path / "storage"
    storage = FileStorage(storage_root)
    monkeypatch.setattr("app.storage.factory.get_storage", lambda _: storage)

    img = Image.new("CMYK", (64, 64), color=(0, 255, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    img_bytes = buf.getvalue()

    storage_key = "derived/ws1/doc1/images/test.png"
    storage.put(storage_key, img_bytes)

    loop_settings = settings(storage_root=storage_root)

    model = ScriptedModel(
        [
            response("search_docs", {"query": "chart"}, "call-search"),
            response("finish_answer", {"text": "Answer from image analysis"}),
        ]
    )

    class SearchInput(BaseModel):
        query: str

    async def search_docs(call, args):
        return ToolResult(
            status="ok",
            summary="found image passage",
            data={
                "passages": [
                    {
                        "chunk_id": "chunk-img-1",
                        "evidence_id": "ev-1",
                        "image_key": storage_key,
                        "excerpt": "[Image: chart.png]",
                        "location": {"type": "image"},
                    }
                ]
            },
        )

    search_tool = Tool(
        name="search_docs",
        description="search",
        arguments=SearchInput,
        execute=search_docs,
    )

    loop = AgentLoop(model, loop_settings, [search_tool], ignore, ignore)
    answer = await loop.run([], "en-IN")
    assert answer.text == "Answer from image analysis"

    second_request_messages = model.requests[1]
    vision_msg = next(
        msg
        for msg in second_request_messages
        if msg["role"] == "user" and isinstance(msg["content"], list)
    )
    assert vision_msg["content"][0]["type"] == "text"
    assert "ev-1" in vision_msg["content"][0]["text"]
    assert vision_msg["content"][1]["type"] == "image_url"
    assert vision_msg["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )


async def test_vision_context_follows_all_replies_in_tool_batch(monkeypatch):
    from pydantic import BaseModel

    class SearchInput(BaseModel):
        query: str

    async def search(call, args):
        return ToolResult(status="ok", summary="image", data={"passages": [{}]})

    batch = ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ModelToolCall(id=identity, name="search", arguments='{"query":"chart"}')
            for identity in ("first", "second")
        ],
    )
    model = ScriptedModel([batch, response("finish_answer", {"text": "done"})])
    loop = AgentLoop(
        model,
        settings(),
        [Tool("search", "search", SearchInput, search)],
        ignore,
        ignore,
    )
    monkeypatch.setattr(
        loop, "_extract_vision_parts", lambda _: [{"type": "text", "text": "visual"}]
    )
    await loop.run([], "en-IN")
    messages = model.requests[1]
    assert [message["role"] for message in messages] == [
        "system",
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert [message["tool_call_id"] for message in messages[2:4]] == ["first", "second"]
    assert len(messages[-1]["content"]) == 2
