"""Database connection setup, schema discovery, and refresh endpoints."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.contracts import Contract
from app.db.models import Connection, Dataset, Source, Workspace
from app.db.session import get_session
from app.sources.connections import (
    ConnectorError,
    decrypt_credentials,
    encrypt_credentials,
    inspect_schema,
    normalize_options,
    test_connection,
)

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


class ConnectionBody(Contract):
    dialect: Literal["mysql", "postgresql"]
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr
    options: dict[str, Any] = Field(default_factory=dict)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("host", "database_name", "username")
    @classmethod
    def validate_non_path_values(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or any(
                character.isspace() or ord(character) < 32 for character in normalized
            )
            or "/" in normalized
            or "\\" in normalized
        ):
            raise ValueError("value contains unsupported characters")
        return normalized

    @field_validator("options")
    @classmethod
    def validate_connection_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_options(value)

    @model_validator(mode="after")
    def default_port(self) -> ConnectionBody:
        if self.port is None:
            self.port = 5432 if self.dialect == "postgresql" else 3306
        if self.display_name is not None:
            self.display_name = self.display_name.strip() or None
        return self


def _connection_values(body: ConnectionBody) -> dict[str, Any]:
    return {
        "dialect": body.dialect,
        "host": body.host,
        "port": body.port or (5432 if body.dialect == "postgresql" else 3306),
        "database_name": body.database_name,
        "username": body.username,
        "password": body.password.get_secret_value(),
        "options": body.options,
    }


def _schema_version(tables: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _workspace_or_404(session: Session, workspace_id: str) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace


def _source_or_404(session: Session, source_id: str) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    if source.kind not in {"mysql", "postgresql"}:
        raise HTTPException(409, "Source is not a database connection")
    return source


def _connection_response(connection: Connection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "source_id": connection.source_id,
        "dialect": connection.dialect,
        "host": connection.host,
        "port": connection.port,
        "database_name": connection.database_name,
        "username": connection.username,
        "options": connection.options,
    }


def _source_response(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "kind": source.kind,
        "display_name": source.display_name,
        "state": source.state,
        "version": source.version,
        "schema_version": source.schema_version,
        "details": source.details,
    }


def _dataset_response(dataset: Dataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "source_id": dataset.source_id,
        "source_version": dataset.source_version,
        "identity": dataset.identity,
        "schema_version": dataset.schema_version,
        "details": dataset.details,
        "storage_key": dataset.storage_key,
        "designation": dataset.designation,
        "lineage": dataset.lineage,
    }


def _inspect_saved_connection(
    connection: Connection, settings: Settings
) -> list[dict[str, Any]]:
    password = decrypt_credentials(connection.encrypted_credentials, settings)
    try:
        return inspect_schema(
            dialect=connection.dialect,
            host=connection.host,
            port=connection.port,
            database_name=connection.database_name,
            username=connection.username,
            password=password,
            options=connection.options,
        )
    except ConnectorError:
        raise
    except Exception:
        raise ConnectorError(
            "schema_inspection_failed",
            "Could not inspect the selected database schema.",
        ) from None


@router.post("/api/workspaces/{workspace_id}/connections/test")
def test_database_connection(
    workspace_id: str, body: ConnectionBody, session: Db
) -> dict[str, Any]:
    _workspace_or_404(session, workspace_id)
    values = _connection_values(body)
    result = test_connection(**values)
    if not result["ok"]:
        return result
    return result


@router.post("/api/workspaces/{workspace_id}/connections", status_code=201)
def create_connection(
    workspace_id: str,
    body: ConnectionBody,
    session: Db,
    settings: AppSettings,
) -> dict[str, Any]:
    _workspace_or_404(session, workspace_id)
    values = _connection_values(body)
    probe = test_connection(**values)
    if not probe["ok"]:
        raise HTTPException(status_code=422, detail=probe["message"])
    try:
        encrypted = encrypt_credentials(values["password"], settings)
    except ConnectorError as error:
        raise HTTPException(status_code=503, detail=error.message) from None
    try:
        tables = inspect_schema(**values)
    except Exception:
        raise HTTPException(
            status_code=422, detail="Connected, but schema inspection failed."
        ) from None

    digest = _schema_version(tables)
    source = Source(
        workspace_id=workspace_id,
        kind=body.dialect,
        display_name=body.display_name or body.database_name,
        state="ready",
        version=1,
        schema_version=digest,
        details={"table_count": len(tables), "schema_version": digest},
    )
    connection = Connection(
        source_id="",
        dialect=body.dialect,
        host=body.host,
        port=body.port or (5432 if body.dialect == "postgresql" else 3306),
        database_name=body.database_name,
        username=body.username,
        encrypted_credentials=encrypted,
        options=body.options,
    )
    session.add(source)
    session.flush()
    connection.source_id = source.id
    session.add(connection)
    datasets = [
        Dataset(
            source_id=source.id,
            source_version=source.version,
            identity=table["identity"],
            schema_version=hashlib.sha256(
                json.dumps(table, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            details=table,
            storage_key=None,
            designation="original",
            lineage=[],
        )
        for table in tables
    ]
    session.add_all(datasets)
    session.commit()
    return {
        "source": _source_response(source),
        "connection": _connection_response(connection),
        "datasets": [_dataset_response(dataset) for dataset in datasets],
    }


@router.get("/api/sources/{source_id}/schema")
def get_source_schema(source_id: str, session: Db) -> dict[str, Any]:
    source = _source_or_404(session, source_id)
    datasets = session.scalars(
        select(Dataset)
        .where(
            Dataset.source_id == source.id,
            Dataset.source_version == source.version,
        )
        .order_by(Dataset.identity)
    ).all()
    return {
        "source_id": source.id,
        "source_version": source.version,
        "schema_version": source.schema_version,
        "datasets": [_dataset_response(dataset) for dataset in datasets],
    }


@router.post("/api/sources/{source_id}/refresh")
def refresh_source_schema(
    source_id: str, session: Db, settings: AppSettings
) -> dict[str, Any]:
    source = _source_or_404(session, source_id)
    connection = session.scalar(
        select(Connection).where(Connection.source_id == source.id)
    )
    if connection is None:
        raise HTTPException(409, "Database connection is unavailable")
    try:
        tables = _inspect_saved_connection(connection, settings)
    except ConnectorError as error:
        raise HTTPException(status_code=422, detail=error.message) from None

    next_version = source.version + 1
    digest = _schema_version(tables)
    source.version = next_version
    source.schema_version = digest
    source.state = "ready"
    source.details = {"table_count": len(tables), "schema_version": digest}
    datasets = [
        Dataset(
            source_id=source.id,
            source_version=next_version,
            identity=table["identity"],
            schema_version=hashlib.sha256(
                json.dumps(table, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            details=table,
            storage_key=None,
            designation="original",
            lineage=[],
        )
        for table in tables
    ]
    session.add_all(datasets)
    session.commit()
    return {
        "source": _source_response(source),
        "datasets": [_dataset_response(dataset) for dataset in datasets],
    }
