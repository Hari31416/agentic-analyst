"""Bounded, deterministic model-context view of durable thread messages.

The database message log remains the source of truth. This module only builds a
request-local view and never edits or summarizes persisted messages in place.
"""

import json
import re
from typing import Any


class ContextLimitExceeded(ValueError):
    """Required user content and durable references exceed the context budget."""


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।])\s+")
_DEFINITION_CUES = re.compile(
    r"\b(defin(?:e|es|ed|ition)|assum(?:e|es|ed|ption)|unit|means|refers to|"
    r"denotes|treated as|calculated as|measured in)\b|है का अर्थ|मान लिया|इकाई",
    re.IGNORECASE,
)


def _metadata(message: dict[str, Any]) -> str:
    details: dict[str, Any] = {}
    sources = list(
        dict.fromkeys(str(item) for item in message.get("selected_source_ids", []))
    )
    references = message.get("references") or {}
    evidence = list(
        dict.fromkeys(str(item) for item in references.get("evidence_ids", []))
    )
    artifacts = list(
        dict.fromkeys(str(item) for item in references.get("artifact_ids", []))
    )
    if sources:
        details["selected_source_ids"] = sources
    if evidence:
        details["evidence_ids"] = evidence
    if artifacts:
        details["artifact_ids"] = artifacts
    if not details:
        return ""
    return (
        "\n[Thread context metadata: "
        + json.dumps(details, separators=(",", ":"))
        + "]"
    )


def _assistant_excerpt(content: str, max_chars: int) -> str:
    """Keep recent answer text and definition/assumption/unit sentences first."""
    if len(content) <= max_chars:
        return content
    sentences = [
        part.strip() for part in _SENTENCE_BOUNDARY.split(content) if part.strip()
    ]
    priority = [i for i, part in enumerate(sentences) if _DEFINITION_CUES.search(part)]
    recent = list(reversed(range(len(sentences))))
    ordered = list(dict.fromkeys([*priority, *recent]))
    kept: list[int] = []
    size = 0
    for index in ordered:
        sentence = sentences[index]
        extra = len(sentence) + (1 if kept else 0)
        if size + extra <= max_chars:
            kept.append(index)
            size += extra
    # Put retained sentences back into their original order for readability.
    chosen = set(kept)
    excerpt = " ".join(
        sentence for index, sentence in enumerate(sentences) if index in chosen
    )
    if len(excerpt) > max_chars:
        return excerpt[:max_chars]
    return excerpt


def select_thread_context(
    history: list[dict[str, Any]], max_characters: int
) -> list[dict[str, str]]:
    """Select a bounded conversational view while retaining durable user facts.

    User messages are kept verbatim because they may contain corrections and
    assumptions. Source/evidence/artifact identifiers are carried separately
    from assistant prose, so compaction cannot sever provenance. Recent assistant
    text gets the remaining budget; older answers yield first.
    """
    required: list[tuple[int, dict[str, str], str]] = []
    assistants: list[tuple[int, dict[str, str], str, str]] = []
    for index, source in enumerate(history):
        role = source.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(source.get("content") or "")
        metadata = _metadata(source)
        item = {"role": role, "content": content + metadata}
        if role == "user":
            required.append((index, item, item["content"]))
        else:
            assistants.append((index, item, content, metadata))

    required_size = sum(len(entry[2]) for entry in required)
    metadata_size = sum(len(entry[3]) for entry in assistants)
    if required_size + metadata_size > max_characters:
        raise ContextLimitExceeded(
            "User messages and durable references exceed context limit"
        )

    remaining = max_characters - required_size - metadata_size
    selected_assistants: dict[int, dict[str, str]] = {}
    # Newest answers are most useful for follow-ups. Older assistant prose is
    # compacted to definition/assumption/unit sentences when space is tight.
    for index, item, content, metadata in reversed(assistants):
        allowance = max(0, remaining)
        excerpt = _assistant_excerpt(content, allowance)
        selected_assistants[index] = {
            "role": "assistant",
            "content": excerpt + metadata,
        }
        remaining -= len(excerpt)

    selected = {index: item for index, item, _ in required}
    selected.update(selected_assistants)
    return [selected[index] for index in sorted(selected)]
