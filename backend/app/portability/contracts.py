from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^assets/[0-9a-f]{64}$")
    owner_type: Literal["source", "dataset", "artifact", "block"]
    owner_id: str = Field(min_length=1, max_length=36)
    purpose: Literal["original", "dataset", "artifact", "image"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0, le=128 * 1024 * 1024)


class PortableManifest(BaseModel):
    """Versioned JSON contract. Entity records are reconstructed by allowlist."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["agentic-rag-analyst-workspace"]
    schema_version: Literal[1]
    workspace: dict[str, Any]
    entities: dict[str, list[dict[str, Any]]]
    assets: list[AssetRecord]
