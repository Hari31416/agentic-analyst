from pydantic import Field, field_validator
from indic_language_utils.languages import DEFAULT_LANGUAGE_REGISTRY, LanguageRegistry
from indic_language_utils.errors import UnsupportedLanguageError

from app.contracts import Contract


class LanguageMetadata(Contract):
    tags: list[str] = Field(default_factory=list)
    confidence: str = "unknown"
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    script: str | None = None
    segments: list[dict[str, object]] = Field(default_factory=list)
    requested_answer_language: str | None = None
    provider: str = "heuristic"
    capabilities: dict[str, bool] = Field(default_factory=dict)

    @property
    def mixed(self) -> bool:
        return len(self.tags) > 1

    @field_validator("tags")
    @classmethod
    def canonicalize(cls, values: list[str]) -> list[str]:
        try:
            return list(
                dict.fromkeys(
                    str(DEFAULT_LANGUAGE_REGISTRY.normalize(v)) for v in values
                )
            )
        except UnsupportedLanguageError as exc:
            raise ValueError("unsupported language tag") from exc


def normalize_language_tag(
    value: str, registry: LanguageRegistry = DEFAULT_LANGUAGE_REGISTRY
) -> str:
    """Return a registry-canonical BCP-47 tag."""
    try:
        return str(registry.normalize(value))
    except UnsupportedLanguageError as exc:
        raise ValueError("unsupported language tag") from exc
