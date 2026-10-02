from pydantic import Field, field_validator
from indic_language_utils.languages import DEFAULT_LANGUAGE_REGISTRY
from indic_language_utils.errors import UnsupportedLanguageError

from app.contracts import Contract


class LanguageMetadata(Contract):
    tags: list[str] = Field(default_factory=list)
    confidence: str = "unknown"

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

    @property
    def mixed(self) -> bool:
        return len(self.tags) > 1
