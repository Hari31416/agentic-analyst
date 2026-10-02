"""OpenAI-compatible Chat Completions transport for the agent runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from app.agent.protocol import ModelResponse, ModelToolCall
from app.config import Settings


class ModelError(RuntimeError):
    """A bounded provider error that is safe to return to the application."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class OpenAICompatibleModel:
    """Call a configured OpenAI-compatible Chat Completions endpoint.

    Streaming is opt-in and still returns a completed ``ModelResponse``. The
    runtime owns incremental UI events, while this adapter assembles fragmented
    provider tool calls into the protocol's stable IDs and argument strings.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        streaming: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._streaming = streaming
        self._http_client = http_client

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        settings = self._settings
        base_url = settings.openai_base_url
        api_key = settings.openai_api_key
        model_name = settings.openai_model
        if not (base_url and api_key and model_name):
            raise ModelError(
                "model_not_configured",
                "Configure OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL.",
            )

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": self._streaming,
        }
        if tools:
            payload["tools"] = tools

        try:
            encoded_request = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ModelError(
                "model_invalid_request", "The model request could not be encoded."
            ) from None
        if len(encoded_request.decode("utf-8")) > settings.max_context_characters:
            raise ModelError(
                "model_context_too_large",
                "The model request exceeds the configured context limit.",
            )

        url = f"{base_url.rstrip('/')}/chat/completions"
        api_key_value = api_key.get_secret_value()
        headers = {
            "Authorization": f"Bearer {api_key_value}",
            "Accept": "text/event-stream" if self._streaming else "application/json",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(float(settings.run_timeout_seconds))

        try:
            if self._http_client is not None:
                return await self._send(
                    self._http_client, url, headers, payload, settings.max_result_bytes
                )
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await self._send(
                    client, url, headers, payload, settings.max_result_bytes
                )
        except ModelError:
            raise
        except httpx.TimeoutException:
            raise ModelError(
                "model_timeout", "The model endpoint timed out.", retryable=True
            ) from None
        except httpx.HTTPError:
            raise ModelError(
                "model_unavailable",
                "The model endpoint could not be reached.",
                retryable=True,
            ) from None

    async def _send(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        max_response_bytes: int,
    ) -> ModelResponse:
        async with client.stream(
            "POST", url, headers=headers, json=payload
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                retryable = (
                    response.status_code in {408, 429} or response.status_code >= 500
                )
                raise ModelError(
                    "model_provider_error",
                    f"The model provider rejected the request (HTTP {response.status_code}).",
                    retryable=retryable,
                )
            if self._streaming:
                state = _StreamState()
                await _read_sse(response, max_response_bytes, state)
                return _stream_response(state, self._settings.max_context_characters)
            body = await _read_limited(response, max_response_bytes)
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ModelError(
                    "model_invalid_response",
                    "The model provider returned invalid JSON.",
                ) from None
            return _completion_response(data, self._settings.max_context_characters)


def create_model(
    settings: Settings,
    *,
    streaming: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleModel | None:
    """Return a model only when the explicit endpoint configuration is complete."""

    if not settings.model_configured:
        return None
    return OpenAICompatibleModel(settings, streaming=streaming, http_client=http_client)


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                raise ModelError(
                    "model_response_too_large",
                    "The model response exceeds the configured size limit.",
                )
        except ValueError:
            # Ignore malformed Content-Length values and enforce the limit while reading.
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ModelError(
                "model_response_too_large",
                "The model response exceeds the configured size limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_sse(response: httpx.Response, limit: int, state: _StreamState) -> None:
    total = 0
    buffer = bytearray()
    event_data: list[str] = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ModelError(
                "model_response_too_large",
                "The model response exceeds the configured size limit.",
            )
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ModelError(
                "model_response_too_large",
                "The model response exceeds the configured size limit.",
            )
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            raw_line = bytes(buffer[:newline]).rstrip(b"\r")
            del buffer[: newline + 1]
            line = _decode_sse_line(raw_line)
            if not line:
                if _consume_sse_event(event_data, state):
                    return
                event_data.clear()
            elif line.startswith("data:"):
                event_data.append(line[5:].lstrip(" "))
    if buffer:
        line = _decode_sse_line(bytes(buffer).rstrip(b"\r"))
        if line.startswith("data:"):
            event_data.append(line[5:].lstrip(" "))
    _consume_sse_event(event_data, state)


def _decode_sse_line(line: bytes) -> str:
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        raise ModelError(
            "model_invalid_response", "The model stream was not valid UTF-8."
        ) from None


def _consume_sse_event(data: list[str], state: _StreamState) -> bool:
    if not data:
        return False
    payload = "\n".join(data)
    if payload == "[DONE]":
        state.done = True
        return True
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        raise ModelError(
            "model_invalid_response", "The model stream contained invalid JSON."
        ) from None
    _add_stream_chunk(chunk, state)
    return False


class _StreamTool:
    def __init__(self) -> None:
        self.id: str | None = None
        self.type: str | None = None
        self.name_parts: list[str] = []
        self.argument_parts: list[str] = []


class _StreamState:
    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tools: dict[int, _StreamTool] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, int] = {}
        self.done = False


def _add_stream_chunk(chunk: Any, state: _StreamState) -> None:
    if not isinstance(chunk, dict):
        raise ModelError(
            "model_invalid_response", "The model stream contained an invalid event."
        )
    state.usage.update(_normalize_usage(chunk.get("usage")))
    choices = chunk.get("choices")
    if choices is None or choices == []:
        return
    if not isinstance(choices, list) or not isinstance(choices[0], dict):
        raise ModelError(
            "model_invalid_response", "The model stream contained an invalid choice."
        )
    choice = choices[0]
    delta = choice.get("delta", {})
    if not isinstance(delta, dict):
        raise ModelError(
            "model_invalid_response", "The model stream contained an invalid delta."
        )
    content = delta.get("content")
    if content is not None:
        if not isinstance(content, str):
            raise ModelError(
                "model_invalid_response", "The model returned unsupported content."
            )
        state.content_parts.append(content)
    fragments = delta.get("tool_calls", [])
    if fragments is not None:
        if not isinstance(fragments, list):
            raise ModelError(
                "model_invalid_response",
                "The model stream contained invalid tool calls.",
            )
        for fragment in fragments:
            _add_tool_fragment(fragment, state)
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None:
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ModelError(
                "model_invalid_response",
                "The model response omitted its finish reason.",
            )
        state.finish_reason = finish_reason


def _add_tool_fragment(fragment: Any, state: _StreamState) -> None:
    if not isinstance(fragment, dict):
        raise ModelError(
            "model_invalid_tool_call", "The model returned an invalid tool call."
        )
    index = fragment.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ModelError(
            "model_invalid_tool_call", "The model returned an invalid tool call."
        )
    tool = state.tools.setdefault(index, _StreamTool())
    call_id = fragment.get("id")
    if call_id is not None:
        if not isinstance(call_id, str) or not call_id:
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned a tool call without an ID.",
            )
        if tool.id is not None and tool.id != call_id:
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned inconsistent tool call IDs.",
            )
        tool.id = call_id
    call_type = fragment.get("type")
    if call_type is not None:
        if call_type != "function":
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned an unsupported tool call type.",
            )
        tool.type = call_type
    function = fragment.get("function", {})
    if not isinstance(function, dict):
        raise ModelError(
            "model_invalid_tool_call", "The model returned an invalid function call."
        )
    name = function.get("name")
    arguments = function.get("arguments")
    if name is not None:
        if not isinstance(name, str):
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned an invalid function name.",
            )
        tool.name_parts.append(name)
    if arguments is not None:
        if not isinstance(arguments, str):
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned invalid function arguments.",
            )
        tool.argument_parts.append(arguments)


def _completion_response(data: Any, max_context_characters: int) -> ModelResponse:
    if not isinstance(data, dict):
        raise ModelError(
            "model_invalid_response", "The model provider returned an invalid response."
        )
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelError(
            "model_invalid_response",
            "The model provider returned no completion choice.",
        )
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        raise ModelError(
            "model_invalid_response", "The model response omitted its finish reason."
        )
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelError(
            "model_invalid_response", "The model provider returned an invalid message."
        )
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ModelError(
            "model_invalid_response", "The model returned unsupported content."
        )
    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise ModelError(
            "model_invalid_response", "The model returned invalid tool calls."
        )
    calls: list[ModelToolCall] = []
    seen_ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ModelError(
                "model_invalid_tool_call", "The model returned an invalid tool call."
            )
        call_id = raw_call.get("id")
        call_type = raw_call.get("type")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned a tool call without an ID.",
            )
        if call_id in seen_ids:
            raise ModelError(
                "model_invalid_tool_call", "The model returned duplicate tool call IDs."
            )
        if call_type != "function" or not isinstance(function, dict):
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned an unsupported tool call.",
            )
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(arguments, str):
            raise ModelError(
                "model_invalid_tool_call", "The model returned an incomplete tool call."
            )
        _validate_arguments(arguments)
        seen_ids.add(call_id)
        calls.append(ModelToolCall(id=call_id, name=name, arguments=arguments))
    usage = _normalize_usage(data.get("usage"))
    return _build_response(content, calls, finish_reason, usage, max_context_characters)


def _stream_response(state: _StreamState, max_context_characters: int) -> ModelResponse:
    if not state.finish_reason:
        raise ModelError(
            "model_invalid_response", "The model response omitted its finish reason."
        )
    calls: list[ModelToolCall] = []
    seen_ids: set[str] = set()
    for index in sorted(state.tools):
        tool = state.tools[index]
        arguments = "".join(tool.argument_parts)
        if not tool.id:
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned a tool call without an ID.",
            )
        if tool.id in seen_ids:
            raise ModelError(
                "model_invalid_tool_call", "The model returned duplicate tool call IDs."
            )
        if tool.type != "function":
            raise ModelError(
                "model_invalid_tool_call",
                "The model returned an unsupported tool call.",
            )
        name = "".join(tool.name_parts)
        if not name:
            raise ModelError(
                "model_invalid_tool_call", "The model returned an incomplete tool call."
            )
        _validate_arguments(arguments)
        seen_ids.add(tool.id)
        calls.append(ModelToolCall(id=tool.id, name=name, arguments=arguments))
    content = "".join(state.content_parts) or None
    return _build_response(
        content, calls, state.finish_reason, state.usage, max_context_characters
    )


def _validate_arguments(arguments: str) -> None:
    if not arguments:
        raise ModelError(
            "model_invalid_tool_call", "The model returned empty function arguments."
        )
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        raise ModelError(
            "model_invalid_tool_call",
            "The model returned malformed function arguments.",
        ) from None
    if not isinstance(parsed, dict):
        raise ModelError(
            "model_invalid_tool_call", "Function arguments must be a JSON object."
        )


def _normalize_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: count
        for key, count in value.items()
        if isinstance(key, str)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
    }


def _build_response(
    content: str | None,
    calls: list[ModelToolCall],
    finish_reason: str,
    usage: dict[str, int],
    max_context_characters: int,
) -> ModelResponse:
    output_characters = len(content or "") + sum(
        len(call.name) + len(call.arguments) for call in calls
    )
    if output_characters > max_context_characters:
        raise ModelError(
            "model_response_too_large",
            "The model response exceeds the configured context limit.",
        )
    try:
        return ModelResponse(
            content=content,
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=usage,
        )
    except ValidationError:
        raise ModelError(
            "model_invalid_response", "The model provider returned an invalid response."
        ) from None
