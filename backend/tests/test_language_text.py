import pytest

from indic_language_utils.languages import (
    LanguageDefinition,
    LanguageRegistry,
    LanguageTag,
)

from app.language.metadata import LanguageMetadata
from app.language.text import (
    analyze_text,
    configured_languages,
    protect_translation_structure,
)


def test_analyze_hindi_numerals_and_mixed_segments_preserves_input():
    text = "कुल १२३ records for scheme S-7"
    metadata = analyze_text(text, "hi", supported_languages=["hi", "en"])
    assert metadata.tags == ["hi-IN", "en-IN"]
    assert metadata.requested_answer_language == "hi-IN"
    assert metadata.script == "Deva"
    assert "१२३" in text
    assert metadata.provider == "script-heuristic"


def test_romanized_hindi_is_best_effort_and_ambiguous_text_is_uncertain():
    roman = analyze_text(
        "Meri scheme ka status batao", supported_languages=["en", "hi"]
    )
    assert roman.tags == ["hi-IN"]
    assert roman.confidence == "best_effort"
    ambiguous = analyze_text("data report", supported_languages=["en", "hi"])
    assert ambiguous.tags == ["en-IN"]
    assert ambiguous.confidence == "uncertain"


def test_configured_registry_language_needs_no_agent_branch():
    registry = LanguageRegistry(
        [
            LanguageDefinition(LanguageTag("en", region="IN"), "English"),
            LanguageDefinition(LanguageTag("fr", region="FR"), "French"),
        ]
    )
    assert configured_languages(["fr-FR"], registry) == [
        {"tag": "fr-FR", "name": "French"}
    ]


def test_protected_structure_round_trip():
    source = "See [doc-7], INR १२,५००, https://example.test/a, `table_1.id`, and ABC_2."
    prepared = protect_translation_structure(source)
    assert "https://example.test/a" not in prepared.text
    assert "INR" not in prepared.text
    assert prepared.restore(prepared.text) == source
    with pytest.raises(ValueError):
        prepared.restore(prepared.text.replace(prepared.tokens[0], ""))


def test_existing_language_metadata_shape_remains_compatible():
    metadata = LanguageMetadata(tags=["hi", "en"])
    assert metadata.tags == ["hi-IN", "en-IN"]
    assert metadata.mixed


def test_registry_configured_bengali_reuses_the_chat_contract():
    from app.api.chat import RunRequest

    request = RunRequest(text="মোট ১২৩ records", answer_language="bn")
    assert request.answer_language == "bn-IN"
    metadata = analyze_text(
        request.text,
        request.answer_language,
        supported_languages=["en-IN", "hi-IN", "bn-IN"],
    )
    assert "bn-IN" in metadata.tags
    assert metadata.requested_answer_language == "bn-IN"
    assert request.text == "মোট ১২৩ records"
