"""Strict, bounded input models for report generation."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)


def _plain_text(value: str, *, max_length: int) -> str:
    if len(value) > max_length:
        raise ValueError("text exceeds safe bounds")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise ValueError("text contains unsupported control characters")
    if re.search(r"data:[^,\s]*;base64,", value, re.IGNORECASE):
        raise ValueError("embedded data URIs are not allowed; use artifact references")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ParagraphBlock(_StrictModel):
    id: StrictStr = Field(min_length=1, max_length=100)
    type: Literal["paragraph"]
    text: StrictStr = Field(max_length=50_000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _plain_text(value, max_length=50_000)


class FigureBlock(_StrictModel):
    id: StrictStr = Field(min_length=1, max_length=100)
    type: Literal["figure"]
    artifact_id: StrictStr = Field(min_length=1, max_length=200)
    caption: StrictStr = Field(default="", max_length=2_000)

    @field_validator("caption")
    @classmethod
    def validate_caption(cls, value: str) -> str:
        return _plain_text(value, max_length=2_000)


class TableBlock(_StrictModel):
    id: StrictStr = Field(min_length=1, max_length=100)
    type: Literal["table"]
    artifact_id: StrictStr = Field(min_length=1, max_length=200)
    caption: StrictStr = Field(default="", max_length=2_000)
    columns: list[StrictStr] = Field(default_factory=list, max_length=256)
    max_rows: int = Field(default=30, ge=1, le=100)

    @field_validator("caption")
    @classmethod
    def validate_caption(cls, value: str) -> str:
        return _plain_text(value, max_length=2_000)

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1_024 for value in values):
            raise ValueError("column name exceeds safe bounds")
        if len(values) != len(set(values)):
            raise ValueError("table columns must be unique")
        return values


ReportBlock = Annotated[
    ParagraphBlock | FigureBlock | TableBlock,
    Field(discriminator="type"),
]


class Section(_StrictModel):
    id: StrictStr = Field(min_length=1, max_length=100)
    heading: StrictStr = Field(min_length=1, max_length=300)
    blocks: list[ReportBlock] = Field(default_factory=list, max_length=250)

    @field_validator("heading")
    @classmethod
    def validate_heading(cls, value: str) -> str:
        return _plain_text(value, max_length=300)


class ReportDocument(_StrictModel):
    title: StrictStr = Field(min_length=1, max_length=200)
    language: Literal["en-IN", "hi-IN"]
    sections: list[Section] = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _plain_text(value, max_length=200)

    @model_validator(mode="after")
    def unique_ids(self) -> ReportDocument:
        ids: list[str] = []
        for section in self.sections:
            ids.append(section.id)
            ids.extend(block.id for block in section.blocks)
        if len(ids) != len(set(ids)):
            raise ValueError("section and block IDs must be unique")
        return self
