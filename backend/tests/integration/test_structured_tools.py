"""Selected-source guardrails and lineage against real PostgreSQL metadata."""

import asyncio
import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent.protocol import ModelToolCall
from app.agent.runtime import NoInput, PythonInput, RunRuntime
from app.config import Settings
from app.db.models import Artifact, Dataset, Evidence, Run, Source, Thread
from app.sources.files import ingest_file
from app.storage.filesystem import FileStorage
from app.tools.structured import RegisterInput, SQLInput, StructuredTools
from tests.integration.test_runtime import db_factory, queued_run

pytestmark = pytest.mark.integration


async def dispatch(runtime, name, args):
    return await runtime.dispatch(
        name,
        ModelToolCall(id=str(uuid4()), name=name, arguments=args.model_dump_json()),
        args,
    )


@pytest.mark.asyncio
async def test_selected_datasets_and_derived_registration(db_factory, tmp_path):
    run_id, task = queued_run(db_factory)
    settings = Settings(
        _env_file=None, storage_backend="filesystem", storage_root=tmp_path
    )
    storage = FileStorage(tmp_path)
    with db_factory() as session:
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        selected, _ = ingest_file(
            session,
            storage,
            thread.workspace_id,
            "selected.csv",
            b"id,amount_inr\n001,10.00\n002,20.00\n",
            max_bytes=1024,
        )
        excluded, _ = ingest_file(
            session,
            storage,
            thread.workspace_id,
            "excluded.csv",
            b"id\n999\n",
            max_bytes=1024,
        )
        selected_dataset = session.scalar(
            select(Dataset).where(Dataset.source_id == selected.id)
        )
        excluded_dataset = session.scalar(
            select(Dataset).where(Dataset.source_id == excluded.id)
        )
        run.selected_source_ids = [selected.id]
        run.config = {
            **run.config,
            "source_versions": {selected.id: 1},
            "selected_dataset_ids": [selected_dataset.id],
        }
        selected_id, excluded_id = selected_dataset.id, excluded_dataset.id
        stored = storage.put(
            f"derived/{thread.workspace_id}/{run_id}/result.csv",
            b"id,total_inr\n001,30.00\n",
        )
        artifact = Artifact(
            run_id=run_id,
            storage_key=stored.key,
            display_name="result.csv",
            media_type="text/csv",
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            lineage=[selected.id],
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
        session.commit()
    runtime = RunRuntime(task, settings, db_factory)
    listed = await dispatch(runtime, "list_sources", NoInput())
    assert listed.data["sources"][0]["datasets"][0]["id"] == selected_id
    denied = await dispatch(
        runtime, "run_sql", SQLInput(sql="SELECT 1", dataset_ids=[UUID(excluded_id)])
    )
    assert denied.status == "failed"
    registered = await dispatch(
        runtime,
        "register_dataset",
        RegisterInput(artifact_id=UUID(artifact_id), display_name="Cleaned result"),
    )
    assert registered.status == "ok"
    repeated = await dispatch(
        runtime, "register_dataset", RegisterInput(artifact_id=UUID(artifact_id))
    )
    assert repeated.data["source_id"] == registered.data["source_id"]
    with db_factory() as session:
        source = session.get(Source, registered.data["source_id"])
        assert (
            source.content_hash == stored.sha256
            and source.details["designation"] == "derived"
        )
        assert source.details["artifact_id"] == artifact_id
    # Registering a result must not broaden the fixed selection of the current run.
    assert len(StructuredTools(runtime).selection()[1]) == 1
