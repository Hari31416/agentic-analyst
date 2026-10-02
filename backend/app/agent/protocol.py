from typing import Any, Protocol

from app.contracts import Contract


class ModelToolCall(Contract):
    id: str
    name: str
    arguments: str


class ModelResponse(Contract):
    content: str | None = None
    tool_calls: list[ModelToolCall] = []
    finish_reason: str
    usage: dict[str, int] = {}


class Model(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...
