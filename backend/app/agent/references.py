"""Conversation-stable model references; durable contracts retain full UUIDs."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, cast
from uuid import UUID

from app.contracts import FinalAnswer, ToolResult

_PREFIXES = {
    "source": "source_",
    "dataset": "dataset_",
    "artifact": "artifact_",
    "evidence": "e",
    "chunk": "chunk_",
}
_ALIAS = re.compile(r"^(source_|dataset_|artifact_|chunk_|e)[1-9][0-9]*$")
_FIELDS = {
    "source_id": "source",
    "source_ids": "source",
    "selected_source_ids": "source",
    "dataset_id": "dataset",
    "dataset_ids": "dataset",
    "selected_dataset_ids": "dataset",
    "input_dataset_ids": "dataset",
    "artifact_id": "artifact",
    "artifact_ids": "artifact",
    "output_artifact_ids": "artifact",
    "input_artifact_ids": "artifact",
    "code_artifact_id": "artifact",
    "code_artifact_ids": "artifact",
    "result_artifact_id": "artifact",
    "evidence_id": "evidence",
    "evidence_ids": "evidence",
    "hop_evidence_ids": "evidence",
    "supporting_evidence_ids": "evidence",
    "chunk_id": "chunk",
    "chunk_ids": "chunk",
}
_COLLECTIONS = {
    "sources": "source",
    "datasets": "dataset",
    "artifacts": "artifact",
    "staged_input_artifacts": "artifact",
    "passages": "chunk",
}
# Source text and computed values are data, even when their keys resemble IDs.
_DATA = {
    "excerpt",
    "text",
    "content",
    "rows",
    "preview",
    "sample",
    "columns",
    "parameters",
    "units",
    "assumptions",
    "stdout",
    "stderr",
}


def _uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


class ModelReferences:
    def __init__(self, aliases: dict[str, str] | None = None):
        self.aliases: dict[str, str] = {}
        self.reverse: dict[tuple[str, str], str] = {}
        for alias, identity in (aliases or {}).items():
            if not isinstance(alias, str) or not isinstance(identity, str):
                raise ValueError("invalid retained reference mapping")
            match = _ALIAS.fullmatch(alias)
            canonical = _uuid(identity)
            if not match or canonical is None:
                raise ValueError("invalid retained reference mapping")
            kind = next(
                kind for kind, prefix in _PREFIXES.items() if prefix == match[1]
            )
            if (kind, canonical) in self.reverse:
                raise ValueError("duplicate retained reference identity")
            self.aliases[alias] = canonical
            self.reverse[kind, canonical] = alias

    def reference(self, kind: str, identity: str) -> str:
        canonical = _uuid(identity)
        if canonical is None:
            return (
                identity  # Test doubles and non-resource descriptors remain unchanged.
            )
        if (kind, canonical) not in self.reverse:
            prefix = _PREFIXES[kind]
            next_number = (
                max(
                    (
                        int(alias[len(prefix) :])
                        for alias in self.aliases
                        if alias.startswith(prefix)
                    ),
                    default=0,
                )
                + 1
            )
            alias = f"{prefix}{next_number}"
            self.aliases[alias] = canonical
            self.reverse[kind, canonical] = alias
        return self.reverse[kind, canonical]

    def resolve(self, kind: str, value: str) -> str:
        if _uuid(value) is not None:
            return value  # Compatibility for retained UUID-based calls.
        prefix = _PREFIXES[kind]
        if (
            not _ALIAS.fullmatch(value)
            or not value.startswith(prefix)
            or value not in self.aliases
        ):
            available = [alias for alias in self.aliases if alias.startswith(prefix)][
                :30
            ]
            raise ValueError(
                f"Unknown {kind} reference '{value[:80]}'. Use returned references: {', '.join(available) or 'none'}"
            )
        return self.aliases[value]

    def schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(schema)

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("format") == "uuid":
                    value.pop("format")
                    value["description"] = "Exact short reference returned by tools."
                for key, item in value.get("properties", {}).items():
                    if key in _FIELDS and isinstance(item, dict):
                        item["description"] = (
                            f"Use exact {_PREFIXES[_FIELDS[key]]}1-style references returned by tools."
                        )
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(result)
        return result

    def arguments(self, arguments: str) -> dict[str, Any]:
        value = json.loads(arguments)

        def visit(item: Any) -> Any:
            if isinstance(item, dict):
                return {
                    key: (
                        val
                        if key in _DATA
                        else (
                            convert(_FIELDS[key], val) if key in _FIELDS else visit(val)
                        )
                    )
                    for key, val in item.items()
                }
            if isinstance(item, list):
                return [visit(val) for val in item]
            return item

        def convert(kind: str, item: Any) -> Any:
            if isinstance(item, str):
                return self.resolve(kind, item)
            if isinstance(item, list):
                return [convert(kind, val) for val in item]
            return item

        return cast(
            dict[str, Any], visit(value)
        )  # Pydantic validates structure and types.

    def answer(self, arguments: str) -> FinalAnswer:
        value = self.arguments(arguments)
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            text = value["text"]
            text = re.sub(
                r"\[(?:evidence:)?(e[0-9]+)\]",
                lambda m: f"[evidence:{self.resolve('evidence', m[1])}]",
                text,
            )
            text = re.sub(
                r"artifact:(artifact_[0-9]+)\b",
                lambda m: f"artifact:{self.resolve('artifact', m[1])}",
                text,
            )
            value["text"] = text
        return FinalAnswer.model_validate(value)

    def text_view(self, text: str) -> str:
        text = re.sub(
            r"\[evidence:([a-fA-F0-9-]{36})\]",
            lambda m: f"[{self.reference('evidence', m[1])}]",
            text,
        )
        text = re.sub(
            r"artifact:([a-fA-F0-9-]{36})",
            lambda m: f"artifact:{self.reference('artifact', m[1])}",
            text,
        )
        # Legacy bare UUID Markdown artifact destinations.
        return re.sub(
            r"\]\(([a-fA-F0-9-]{36})(?=[\s)])",
            lambda m: f"](artifact:{self.reference('artifact', m[1])}",
            text,
        )

    def view(self, value: Any, collection: str | None = None) -> Any:
        if isinstance(value, list):
            return [self.view(item, collection) for item in value]
        if not isinstance(value, dict):
            return value
        output: dict[str, Any] = {}
        for key, item in value.items():
            kind = _FIELDS.get(key) or (collection if key == "id" else None)
            if kind:
                output[key] = self._identities(kind, item)
            elif key in _DATA:
                output[key] = item
            elif key == "source_versions" and isinstance(item, dict):
                output[key] = {
                    self.reference("source", identity): version
                    for identity, version in item.items()
                }
            elif (
                key == "sql_table"
                and isinstance(item, str)
                and re.fullmatch(r"data_[a-f0-9]{32}", item)
            ):
                output[key] = self.reference("dataset", item[5:])
            elif key == "guest_path" and isinstance(item, str):
                output[key] = self._path_view(item)
            elif key in {"summary", "message"} and isinstance(item, str):
                # Only replace already-known resource IDs in application diagnostics.
                output[key] = self._diagnostic_view(item)
            else:
                output[key] = self.view(item, _COLLECTIONS.get(key))
        return output

    def _identities(self, kind: str, value: Any) -> Any:
        if isinstance(value, list):
            return [self._identities(kind, item) for item in value]
        return self.reference(kind, value) if isinstance(value, str) else value

    def _path_view(self, value: str) -> str:
        match = re.fullmatch(r"/workspace/inputs/([a-fA-F0-9-]{36})(\.csv)?", value)
        if not match:
            return value
        kind = "dataset" if match[2] else "artifact"
        return f"/workspace/inputs/{self.reference(kind, match[1])}{match[2] or ''}"

    def _diagnostic_view(self, value: str) -> str:
        for (kind, identity), alias in self.reverse.items():
            value = value.replace(identity, alias)
            if kind == "dataset":
                value = value.replace("data_" + UUID(identity).hex, alias)
        return value

    def result(self, result: ToolResult) -> str:
        payload = result.model_dump(mode="json")
        # Register declared references before traversing descriptors and diagnostics.
        for kind in ("evidence", "artifact"):
            self._identities(kind, payload[f"{kind}_ids"])
        view = self.view(payload)
        return json.dumps(view, ensure_ascii=False, separators=(",", ":"))

    def history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for message in history:
            item = self.view(message)
            item["content"] = self.text_view(str(message.get("content") or ""))
            output.append(item)
        return output
