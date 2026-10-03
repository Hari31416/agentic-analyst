"""Best-effort text language metadata and structure protection."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from indic_language_utils.languages import DEFAULT_LANGUAGE_REGISTRY, LanguageRegistry

from app.language.metadata import LanguageMetadata, normalize_language_tag

_SCRIPT_LANGUAGE = {
    "DEVANAGARI": "hi-IN",
    "BENGALI": "bn-IN",
    "GURMUKHI": "pa-IN",
    "GUJARATI": "gu-IN",
    "ORIYA": "or-IN",
    "TAMIL": "ta-IN",
    "TELUGU": "te-IN",
    "KANNADA": "kn-IN",
    "MALAYALAM": "ml-IN",
    "ARABIC": "ur-IN",
}
_ROMAN_HINDI = {
    "hai",
    "hain",
    "ka",
    "ki",
    "ke",
    "kya",
    "kyun",
    "mein",
    "mera",
    "meri",
    "ko",
    "se",
    "hai",
    "batao",
    "dikhao",
    "kitna",
    "kitne",
    "karo",
    "karna",
    "chahiye",
    "aur",
    "nahi",
    "nahin",
    "kahan",
    "kaise",
}
_SCRIPT_RE = re.compile(
    r"[\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0a80-\u0aff\u0b00-\u0bff\u0b80-\u0bff\u0c00-\u0c7f\u0c80-\u0cff\u0d00-\u0d7f\u0600-\u06ff]+"
)


def _script_name(text: str) -> str | None:
    counts: dict[str, int] = {}
    for char in text:
        name = unicodedata.name(char, "")
        for script in (
            "DEVANAGARI",
            "BENGALI",
            "GURMUKHI",
            "GUJARATI",
            "ORIYA",
            "TAMIL",
            "TELUGU",
            "KANNADA",
            "MALAYALAM",
            "ARABIC",
            "LATIN",
        ):
            if script in name:
                counts[script] = counts.get(script, 0) + 1
                break
    non_latin = {key: count for key, count in counts.items() if key != "LATIN"}
    selected = non_latin or counts
    return max(selected, key=selected.__getitem__) if counts else None


def configured_languages(
    supported_languages: list[str] | tuple[str, ...],
    registry: LanguageRegistry = DEFAULT_LANGUAGE_REGISTRY,
) -> list[dict[str, str]]:
    """Return configured, canonical registry entries suitable for a status API."""
    definitions = {str(item.tag): item.name for item in registry.definitions()}
    result = []
    for value in supported_languages:
        tag = normalize_language_tag(value, registry)
        result.append({"tag": tag, "name": definitions.get(tag, tag)})
    return result


def analyze_text(
    text: str,
    answer_language: str | None = None,
    *,
    supported_languages: list[str] | tuple[str, ...] | None = None,
    registry: LanguageRegistry = DEFAULT_LANGUAGE_REGISTRY,
) -> LanguageMetadata:
    """Annotate scripts and likely language segments without changing source text."""
    allowed = (
        tuple(supported_languages)
        if supported_languages is not None
        else tuple(str(item.tag) for item in registry.definitions())
    )
    canonical_allowed = {normalize_language_tag(value, registry) for value in allowed}
    requested = (
        normalize_language_tag(answer_language, registry) if answer_language else None
    )
    if requested is not None and requested not in canonical_allowed:
        raise ValueError("requested answer language is unavailable")

    segments: list[dict[str, object]] = []
    for match in _SCRIPT_RE.finditer(text):
        script_name = _script_name(match.group())
        language = _SCRIPT_LANGUAGE.get(script_name or "")
        if language and language in canonical_allowed:
            segments.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "language": language,
                    "script": _script_code(script_name),
                }
            )

    words = re.findall(r"[A-Za-z]+", text)
    roman_hindi = sum(word.lower() in _ROMAN_HINDI for word in words)
    has_latin = bool(words)
    if has_latin:
        likely = (
            "hi-IN"
            if roman_hindi >= 2 and roman_hindi / max(1, len(words)) >= 0.25
            else "en-IN"
        )
        if likely in canonical_allowed:
            segments.append(
                {
                    "start": 0,
                    "end": len(text),
                    "text": text,
                    "language": likely,
                    "script": "Latn",
                }
            )

    tags = list(dict.fromkeys(str(item["language"]) for item in segments))
    script = _script_code(_script_name(text))
    uncertain = not tags or (has_latin and roman_hindi < 2)
    return LanguageMetadata(
        tags=tags,
        confidence="uncertain" if uncertain else "best_effort",
        confidence_score=None,  # Heuristic labels are not calibrated probabilities.
        script=script,
        segments=segments,
        requested_answer_language=requested,
        provider="script-heuristic",
        capabilities={"text_metadata": True, "translation": False},
    )


def _script_code(name: str | None) -> str | None:
    return {
        "DEVANAGARI": "Deva",
        "BENGALI": "Beng",
        "GURMUKHI": "Guru",
        "GUJARATI": "Gujr",
        "ORIYA": "Orya",
        "TAMIL": "Taml",
        "TELUGU": "Telu",
        "KANNADA": "Knda",
        "MALAYALAM": "Mlym",
        "ARABIC": "Arab",
        "LATIN": "Latn",
    }.get(name or "")


_PROTECTED = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|https?://[^\s)>]+|\b[A-Z][A-Z0-9_]{1,}\b|"
    r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b|"
    r"\b\d[\d,._:/%-]*(?:\s?(?:INR|USD|Rs\.?|₹|%|kg|km|years?))?\b|"
    r"\[\^?[\w.-]+\]|\b(?:[A-Z]{2,}[0-9]+|[A-Za-z]+_[A-Za-z0-9_]+)\b"
)


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]
    originals: tuple[str, ...]

    def restore(self, translated: str) -> str:
        for token, original in zip(self.tokens, self.originals, strict=True):
            if translated.count(token) != 1:
                raise ValueError(
                    "translated text omitted or duplicated protected structure"
                )
            translated = translated.replace(token, original)
        return translated


def protect_translation_structure(text: str) -> ProtectedText:
    """Replace likely literals and structured spans with stable placeholders."""
    originals: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = f"⟦ILUP{len(originals):04d}⟧"
        originals.append(match.group())
        return token

    protected = _PROTECTED.sub(replace, text)
    tokens = tuple(f"⟦ILUP{index:04d}⟧" for index in range(len(originals)))
    return ProtectedText(protected, tokens, tuple(originals))
