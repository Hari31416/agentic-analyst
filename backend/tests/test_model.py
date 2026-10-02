import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.agent.model import ModelError, OpenAICompatibleModel, create_model
from app.config import Settings, get_settings


def configured_settings(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "openai_base_url": "https://models.example/v1/",
        "openai_api_key": SecretStr("provider-secret"),
        "openai_model": "test-model",
        "run_timeout_seconds": 2,
        "max_context_characters": 10000,
        "max_result_bytes": 4096,
    }
    values.update(changes)
    return Settings(**values)


def json_response(message: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    choice = {"finish_reason": "stop", "message": message}
    choice.update(overrides.pop("choice", {}))
    body = {
        "choices": [choice],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_non_streaming_normalizes_content_tool_calls_and_usage() -> None:
    request_details: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_details["url"] = str(request.url)
        request_details["authorization"] = request.headers.get("authorization")
        request_details["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=json_response(
                {
                    "role": "assistant",
                    "content": "I will calculate that.",
                    "tool_calls": [
                        {
                            "id": "call_sum_1",
                            "type": "function",
                            "function": {
                                "name": "sum_values",
                                "arguments": '{"left":2,"right":3}',
                            },
                        }
                    ],
                },
                choice={"finish_reason": "tool_calls"},
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                    "ignored": "x",
                },
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatibleModel(configured_settings(), http_client=client)
        response = await model.complete(
            [{"role": "user", "content": "What is 2 + 3?"}],
            [{"type": "function", "function": {"name": "sum_values"}}],
        )

    assert request_details["url"] == "https://models.example/v1/chat/completions"
    assert request_details["authorization"] == "Bearer provider-secret"
    assert request_details["payload"]["stream"] is False
    assert "stream_options" not in request_details["payload"]
    assert response.content == "I will calculate that."
    assert response.finish_reason == "tool_calls"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
    }
    assert [call.model_dump() for call in response.tool_calls] == [
        {"id": "call_sum_1", "name": "sum_values", "arguments": '{"left":2,"right":3}'}
    ]


def sse_event(data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {payload}\r\n\r\n".encode()


@pytest.mark.asyncio
async def test_stream_reassembles_interleaved_tool_call_fragments() -> None:
    body = b"".join(
        [
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "Checking ",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_a",
                                        "type": "function",
                                        "function": {
                                            "name": "sum_",
                                            "arguments": '{"left":2,',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "call_b",
                                        "type": "function",
                                        "function": {
                                            "name": "multiply",
                                            "arguments": '{"left":4,',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_event(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "the result.",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "values",
                                            "arguments": '"right":3}',
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "function": {"arguments": '"right":5}'},
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            sse_event("[DONE]"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        assert "stream_options" not in json.loads(request.content)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatibleModel(
            configured_settings(), streaming=True, http_client=client
        )
        response = await model.complete([{"role": "user", "content": "Calculate."}], [])

    assert response.content == "Checking the result."
    assert response.finish_reason == "tool_calls"
    assert [(call.id, call.name, call.arguments) for call in response.tool_calls] == [
        ("call_a", "sum_values", '{"left":2,"right":3}'),
        ("call_b", "multiply", '{"left":4,"right":5}'),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        (None, "model_invalid_response"),
        ({"role": "assistant", "content": "no finish"}, "model_invalid_response"),
        (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            },
            "model_invalid_tool_call",
        ),
        (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "same",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    },
                    {
                        "id": "same",
                        "type": "function",
                        "function": {"name": "y", "arguments": "{}"},
                    },
                ],
            },
            "model_invalid_tool_call",
        ),
        (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call",
                        "type": "other",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            },
            "model_invalid_tool_call",
        ),
        (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call",
                        "type": "function",
                        "function": {"name": "x", "arguments": "not-json"},
                    }
                ],
            },
            "model_invalid_tool_call",
        ),
    ],
)
async def test_rejects_malformed_provider_responses(
    message: dict[str, Any] | None, expected_code: str
) -> None:
    if message is None:
        body = b"not json"
    else:
        response = json_response(message)
        if message.get("content") == "no finish":
            response["choices"][0].pop("finish_reason")
        body = json.dumps(response).encode()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        model = OpenAICompatibleModel(configured_settings(), http_client=client)
        with pytest.raises(ModelError) as error:
            await model.complete([], [])

    assert error.value.code == expected_code
    assert "provider-secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "retryable"), [(401, False), (429, True), (503, True)]
)
async def test_provider_http_errors_are_safe(status: int, retryable: bool) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=b"provider echoed provider-secret and private data",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatibleModel(configured_settings(), http_client=client)
        with pytest.raises(ModelError) as error:
            await model.complete([], [])

    assert error.value.code == "model_provider_error"
    assert error.value.retryable is retryable
    assert "provider-secret" not in str(error.value)
    assert "private data" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_does_not_expose_transport_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider-secret appeared in exception")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatibleModel(configured_settings(), http_client=client)
        with pytest.raises(ModelError) as error:
            await model.complete([], [])

    assert error.value.code == "model_timeout"
    assert error.value.retryable
    assert "provider-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_response_and_request_context_limits_are_enforced() -> None:
    oversized = json.dumps(
        json_response({"role": "assistant", "content": "x" * 80})
    ).encode()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=oversized)
        )
    ) as client:
        model = OpenAICompatibleModel(
            configured_settings(max_result_bytes=20), http_client=client
        )
        with pytest.raises(ModelError) as error:
            await model.complete([], [])
    assert error.value.code == "model_response_too_large"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("request should not be sent")
        )
    ) as client:
        model = OpenAICompatibleModel(
            configured_settings(max_context_characters=12), http_client=client
        )
        with pytest.raises(ModelError) as error:
            await model.complete([{"role": "user", "content": "this is too long"}], [])
    assert error.value.code == "model_context_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        {
            "index": 0,
            "id": "",
            "type": "function",
            "function": {"name": "x", "arguments": "{}"},
        },
        {
            "index": 0,
            "id": "call",
            "type": "custom",
            "function": {"name": "x", "arguments": "{}"},
        },
        {
            "index": 0,
            "id": "call",
            "type": "function",
            "function": {"name": "x", "arguments": "{"},
        },
    ],
)
async def test_stream_rejects_empty_id_unknown_type_and_bad_arguments(
    call: dict[str, Any],
) -> None:
    chunk = {
        "choices": [{"delta": {"tool_calls": [call]}, "finish_reason": "tool_calls"}]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=sse_event(chunk) + sse_event("[DONE]")
            )
        )
    ) as client:
        model = OpenAICompatibleModel(
            configured_settings(), streaming=True, http_client=client
        )
        with pytest.raises(ModelError) as error:
            await model.complete([], [])
    assert error.value.code == "model_invalid_tool_call"


def test_factory_has_no_implicit_provider_fallback() -> None:
    settings = Settings(_env_file=None)
    assert create_model(settings) is None
    assert create_model(configured_settings()) is not None


LIVE_SUM_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "sum_values",
            "description": "Return the sum of two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "left": {"type": "integer"},
                    "right": {"type": "integer"},
                },
                "required": ["left", "right"],
                "additionalProperties": False,
            },
        },
    }
]


@pytest.mark.asyncio
@pytest.mark.live_model
@pytest.mark.live
@pytest.mark.parametrize(
    ("prompt", "language"),
    [
        (
            "Use the sum_values tool to add 17 and 25. Then explain the result in English.",
            "en",
        ),
        (
            "sum_values टूल का उपयोग करके 17 और 25 जोड़ें। फिर परिणाम हिंदी में बताएं।",
            "hi",
        ),
    ],
)
async def test_live_model_tool_round_trip_in_english_and_hindi(
    prompt: str, language: str
) -> None:
    if os.environ.get("LIVE_MODEL_TESTS") != "1":
        pytest.skip("Set LIVE_MODEL_TESTS=1 to make live model requests.")
    settings = get_settings()
    if not settings.model_configured:
        pytest.skip("The configured model endpoint is missing credentials.")
    print(
        "Live model probe: "
        f"model={settings.openai_model} "
        f"endpoint_host={urlsplit(settings.openai_base_url or '').hostname} "
        "protocol=chat-completions streaming=disabled"
    )
    model = create_model(settings)
    assert model is not None

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    first = await model.complete(messages, LIVE_SUM_TOOL)
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call.name == "sum_values"
    arguments = json.loads(call.arguments)
    assert arguments == {"left": 17, "right": 25}
    messages.extend(
        [
            {
                "role": "assistant",
                "content": first.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call.id, "content": '{"sum":42}'},
        ]
    )
    final = await model.complete(messages, LIVE_SUM_TOOL)
    assert final.content
    assert re.search(
        r"42|forty.?two|forty two|बयालीस|बयालिस", final.content, re.IGNORECASE
    )
    if language == "hi":
        assert any("\u0900" <= char <= "\u097f" for char in final.content)
