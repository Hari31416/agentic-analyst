"""Bounded, deterministic model-context view of durable thread messages."""

import json
import re
from dataclasses import dataclass
from typing import Any


class ContextLimitExceeded(ValueError):
    """Required user content, facts, and references exceed the context budget."""


@dataclass(frozen=True)
class ContextView:
    messages: list[dict[str, str]]
    compacted: bool
    original_assistant_characters: int
    retained_assistant_characters: int
    omitted_assistant_messages: int
    protected_fact_sentences: int

    @property
    def dropped_assistant_characters(self) -> int:
        return self.original_assistant_characters - self.retained_assistant_characters


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।])\s+")
_FACT_CUES = re.compile(
    r"\b(defin(?:e|es|ed|ition)|assum(?:e|es|ed|ption)|unit|means|refers to|"
    r"denotes|treated as|calculated as|measured in|is in|reported in|"
    r"inr|usd|eur|rupees?|percent(?:age)?|kilograms?|kg|kilometers?|km|"
    r"miles?|hours?|days?|months?|years?)\b|"
    r"है का अर्थ|मान लिया|इकाई",
    re.IGNORECASE,
)


def _metadata(message: dict[str, Any]) -> str:
    details: dict[str, Any] = {}
    for key in ("selected_source_ids", "selected_dataset_ids"):
        values = list(dict.fromkeys(str(item) for item in message.get(key, [])))
        if values:
            details[key] = values
    source_versions = message.get("source_versions") or {}
    if source_versions:
        details["source_versions"] = source_versions
    references = message.get("references") or {}
    for key in ("evidence_ids", "artifact_ids"):
        values = list(dict.fromkeys(str(item) for item in references.get(key, [])))
        if values:
            details[key] = values
    if not details:
        return ""
    return (
        "\n[Thread context metadata: "
        + json.dumps(details, separators=(",", ":"))
        + "]"
    )


def _sentences(content: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(content) if part.strip()]


def _render(sentences: list[str], indices: set[int]) -> str:
    return " ".join(sentence for i, sentence in enumerate(sentences) if i in indices)


def select_thread_context(
    history: list[dict[str, Any]], max_characters: int
) -> ContextView:
    """Preserve user turns and all detected facts, then fill with recent prose.

    Required facts include sentences that state definitions, assumptions, units,
    or calculation interpretations. If these and durable references cannot fit,
    selection fails explicitly instead of silently dropping them. Persisted
    messages are never changed.
    """
    required: list[tuple[int, dict[str, str], int]] = []
    assistants: list[tuple[int, str, str, list[str], set[int]]] = []
    for index, source in enumerate(history):
        role = source.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(source.get("content") or "")
        metadata = _metadata(source)
        if role == "user":
            combined = content + metadata
            required.append((index, {"role": role, "content": combined}, len(combined)))
        else:
            sentences = _sentences(content)
            facts = {
                i for i, sentence in enumerate(sentences) if _FACT_CUES.search(sentence)
            }
            assistants.append((index, content, metadata, sentences, facts))

    user_size = sum(size for _, _, size in required)
    metadata_size = sum(len(metadata) for _, _, metadata, _, _ in assistants)
    fact_size = sum(
        len(_render(sentences, facts)) for _, _, _, sentences, facts in assistants
    )
    if user_size + metadata_size + fact_size > max_characters:
        raise ContextLimitExceeded(
            "User messages, definitions, assumptions, units, and durable references exceed context limit"
        )

    remaining = max_characters - user_size - metadata_size - fact_size
    selected_facts = {index: set(facts) for index, _, _, _, facts in assistants}
    selected_other: dict[int, set[int]] = {index: set() for index, *_ in assistants}
    # Protect facts across all turns before allocating space to recent prose.
    for index, _, _, sentences, facts in reversed(assistants):
        for sentence_index in reversed(range(len(sentences))):
            if sentence_index in facts:
                continue
            sentence = sentences[sentence_index]
            separator = 1 if selected_facts[index] or selected_other[index] else 0
            if len(sentence) + separator <= remaining:
                selected_other[index].add(sentence_index)
                remaining -= len(sentence) + separator

    selected = {index: item for index, item, _ in required}
    original_chars = retained_chars = omitted = fact_count = 0
    for index, content, metadata, sentences, facts in assistants:
        excerpt = _render(sentences, selected_facts[index] | selected_other[index])
        original_chars += len(content)
        retained_chars += len(excerpt)
        omitted += int(bool(content) and not excerpt)
        fact_count += len(facts)
        selected[index] = {"role": "assistant", "content": excerpt + metadata}

    return ContextView(
        messages=[selected[index] for index in sorted(selected)],
        compacted=retained_chars < original_chars,
        original_assistant_characters=original_chars,
        retained_assistant_characters=retained_chars,
        omitted_assistant_messages=omitted,
        protected_fact_sentences=fact_count,
    )
