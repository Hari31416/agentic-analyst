"""A deliberately small Plotly JSON contract; executable extensions are rejected."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

Scalar = StrictStr | StrictInt | StrictFloat


def _safe_label(value: str) -> str:
    if len(value) > 500 or re.search(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError("chart labels must be short plain text")
    if re.search(r"javascript\s*:", value, re.IGNORECASE):
        raise ValueError("chart labels cannot contain active URLs")
    return value


class ChartTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["bar", "scatter", "pie"]
    name: StrictStr | None = Field(default=None, max_length=120)
    x: list[Scalar] | None = Field(default=None, max_length=5000)
    y: list[Scalar] | None = Field(default=None, max_length=5000)
    mode: Literal["lines", "markers", "lines+markers"] | None = None
    labels: list[StrictStr] | None = Field(default=None, max_length=5000)
    values: list[StrictInt | StrictFloat] | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def name_plain_text(cls, value: str | None) -> str | None:
        return _safe_label(value) if value is not None else None

    @field_validator("x")
    @classmethod
    def x_values_safe(cls, values: list[Scalar] | None) -> list[Scalar] | None:
        if values is None:
            return None
        for value in values:
            if isinstance(value, str):
                _safe_label(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("chart values must be finite")
        return values

    @field_validator("y", "values")
    @classmethod
    def numeric_finite(cls, values: list[Any] | None) -> list[Any] | None:
        if values is not None and any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or isinstance(item, float)
            and not math.isfinite(item)
            for item in values
        ):
            raise ValueError("chart values must be finite numbers")
        return values

    @field_validator("labels")
    @classmethod
    def labels_plain_text(cls, values: list[str] | None) -> list[str] | None:
        return [_safe_label(value) for value in values] if values is not None else None

    @model_validator(mode="after")
    def valid_shape(self) -> ChartTrace:
        if self.type == "pie":
            if (
                self.labels is None
                or self.values is None
                or self.x is not None
                or self.y is not None
            ):
                raise ValueError("pie traces require labels and values only")
            if len(self.labels) != len(self.values):
                raise ValueError("pie labels and values must have equal lengths")
        else:
            if self.y is None or self.labels is not None or self.values is not None:
                raise ValueError("bar/scatter traces require y values")
            if self.x is not None and len(self.x) != len(self.y):
                raise ValueError("x and y must have equal lengths")
            if self.mode is not None and self.type != "scatter":
                raise ValueError("mode is supported only for scatter traces")
        return self


class AxisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: StrictStr | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def title_plain_text(cls, value: str | None) -> str | None:
        return _safe_label(value) if value is not None else None


class ChartLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: StrictStr | None = Field(default=None, max_length=200)
    xaxis: AxisSpec | None = None
    yaxis: AxisSpec | None = None
    barmode: Literal["group", "stack", "overlay"] | None = None

    @field_validator("title")
    @classmethod
    def title_plain_text(cls, value: str | None) -> str | None:
        return _safe_label(value) if value is not None else None


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    data: list[ChartTrace] = Field(min_length=1, max_length=10)
    layout: ChartLayout = Field(default_factory=ChartLayout)
    config: dict[str, Literal[True, False]] = Field(
        default_factory=lambda: {"responsive": True, "displayModeBar": False}
    )

    @field_validator("config")
    @classmethod
    def supported_config(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) - {"responsive", "displayModeBar"}:
            raise ValueError("unsupported chart config option")
        return value

    @classmethod
    def from_json_bytes(cls, content: bytes) -> ChartSpec:
        return cls.model_validate_json(content)

    def plotly(self) -> dict[str, Any]:
        """Return only the display-safe Plotly subset expected by the frontend."""
        return {
            "data": [trace.model_dump(exclude_none=True) for trace in self.data],
            "layout": self.layout.model_dump(exclude_none=True),
            "config": {"responsive": True, "displayModeBar": False},
        }
