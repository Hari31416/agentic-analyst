"""Real PostgreSQL connector + file snapshots -> microVM -> durable lineage."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text, create_engine
from sqlalchemy.engine import make_url

from app.agent.protocol import ModelToolCall
from app.agent.runtime import RunRuntime
from app.config import Settings
from app.db.models import (
    Artifact,
    Connection,
    Dataset,
    Evidence,
    Run,
    Source,
    Thread,
    Job,
)
from app.sources.connections import encrypt_credentials
from app.sources.files import profile_upload
from app.storage.filesystem import FileStorage
from app.workers.queue import claim as claim_job
from app.tools.analysis import AnalyzeInput
from app.tools.structured import SQLInput, RegisterInput
from tests.integration.test_runtime import db_factory, queued_run  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.live_sandbox]


@pytest.mark.asyncio
async def test_mixed_query_file_analysis_retains_hashes_lineage_and_reuse(
    db_factory, tmp_path, monkeypatch
):
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("explicit microVM milestone opt-in")
    settings = Settings()
    target = make_url(settings.database_url.get_secret_value()).set(
        database="analyst_eval"
    )
    engine = create_engine(target)
    with engine.connect() as connection:
        before = list(
            connection.execute(
                text(
                    "SELECT application_id,grant_amount_inr FROM public.synthetic_applications ORDER BY application_id"
                )
            )
        )
    store = FileStorage(tmp_path)
    for module in [
        "app.agent.runtime",
        "app.tools.structured",
        "app.tools.analysis",
        "app.storage.factory",
    ]:
        monkeypatch.setattr(f"{module}.get_storage", lambda _settings: store)
    run_id, claim = queued_run(
        db_factory, config={"answer_language": "en-IN", "source_versions": {}}
    )
    content = b"application_id,region\nAPP-001,North\nAPP-002,North\nAPP-003,South\nAPP-004,South\nAPP-005,West\n"
    original = store.put("originals/regions.csv", content)
    profile = profile_upload("regions.csv", content)[0]
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        db_source = Source(
            workspace_id=thread.workspace_id,
            kind="postgresql",
            display_name="Synthetic DB",
            state="ready",
            details={},
        )
        file_source = Source(
            workspace_id=thread.workspace_id,
            kind="csv",
            display_name="regions.csv",
            state="ready",
            storage_key=original.key,
            content_hash=original.sha256,
        )
        session.add_all([db_source, file_source])
        session.flush()
        db_dataset = Dataset(
            source_id=db_source.id,
            source_version=1,
            identity="public.synthetic_applications",
            schema_version="test-db-v1",
            details={
                "schema": "public",
                "columns": [{"name": "grant_amount_inr", "hints": {"unit": "INR"}}],
            },
        )
        file_dataset = Dataset(
            source_id=file_source.id,
            source_version=1,
            identity="data",
            schema_version="file-profile-v1",
            details=profile["details"],
        )
        session.add_all([db_dataset, file_dataset])
        session.flush()
        session.add(
            Connection(
                source_id=db_source.id,
                dialect="postgresql",
                host=target.host,
                port=target.port,
                database_name=target.database,
                username=target.username,
                encrypted_credentials=encrypt_credentials(
                    target.password or "", settings
                ),
                options={"ssl_mode": "disable"},
            )
        )
        run.selected_source_ids = [db_source.id, file_source.id]
        run.config = {
            **run.config,
            "source_versions": {db_source.id: 1, file_source.id: 1},
            "selected_dataset_ids": [db_dataset.id, file_dataset.id],
        }
        db_source_id, file_source_id, file_dataset_id = (
            db_source.id,
            file_source.id,
            file_dataset.id,
        )
    runtime = RunRuntime(claim, settings, db_factory)

    def call(name):
        return ModelToolCall(id=str(uuid4()), name=name, arguments="{}")

    try:
        ambiguous = await runtime.dispatch(
            "run_sql",
            call("run_sql"),
            SQLInput(sql="SELECT application_id FROM public.synthetic_applications"),
        )
        assert (
            ambiguous.status == "failed"
            and ambiguous.error.code == "invalid_tool_input"
        )
        assert "source_id" in ambiguous.error.message
        queried = await runtime.dispatch(
            "run_sql",
            call("run_sql"),
            SQLInput(
                source_id=db_source_id,
                sql="SELECT application_id, grant_amount_inr FROM public.synthetic_applications ORDER BY application_id",
                max_rows=10,
            ),
        )
        assert queried.status == "ok", queried.model_dump()
        snapshot = next(
            a for a in queried.data["artifacts"] if a["display_name"] == "result.csv"
        )
        result = await runtime.dispatch(
            "analyze_data",
            call("analyze_data"),
            AnalyzeInput(
                inputs=[
                    {"alias": "grants", "artifact_id": snapshot["id"]},
                    {"alias": "regions", "dataset_id": file_dataset_id},
                ],
                result_frame="grants",
                operations=[
                    {
                        "kind": "join",
                        "frame": "grants",
                        "columns": ["application_id"],
                        "right_frame": "regions",
                        "right_on": ["application_id"],
                        "relationship": "one_to_one",
                    },
                    {
                        "kind": "convert",
                        "frame": "grants",
                        "columns": ["grant_amount_inr"],
                        "data_type": "number",
                    },
                    {
                        "kind": "aggregate",
                        "frame": "grants",
                        "columns": ["region"],
                        "metrics": [
                            {
                                "column": "grant_amount_inr",
                                "function": "sum",
                                "output": "total_inr",
                            }
                        ],
                    },
                ],
                assumptions=["One application per identifier; grants measured in INR."],
            ),
        )
        assert result.status == "ok", result.model_dump()
        assert result.data["analysis"]["rows"] == [
            {"region": "North", "total_inr": "25000.00"},
            {"region": "South", "total_inr": "17000.01"},
            {"region": "West", "total_inr": "5000.50"},
        ]
        assert result.data["analysis"]["units"]["total_inr"] == "INR"
        output = next(
            a for a in result.data["artifacts"] if a["display_name"] == "result.csv"
        )
        reused = await runtime.dispatch(
            "register_dataset",
            call("register_dataset"),
            RegisterInput(artifact_id=output["id"], display_name="Regional grants.csv"),
        )
        assert reused.status == "ok"
        with db_factory() as session:
            evidence = session.get(Evidence, str(result.evidence_ids[0]))
            assert (
                evidence.details["input_snapshots"][0]["sha256"] == snapshot["sha256"]
            )
            artifact = session.get(Artifact, output["id"])
            assert f"artifact:{snapshot['id']}" in artifact.lineage
            derived = session.get(Dataset, reused.data["dataset_ids"][0])
            assert f"artifact:{output['id']}" in derived.lineage
            assert store.verify(original.key, original.sha256, original.byte_size)
        # A later run selects only the derived dataset; the guest reads the
        # retained snapshot, while output lineage still names the original inputs.
        with db_factory() as session, session.begin():
            previous = session.get(Run, run_id)
            derived_source = session.get(Source, reused.data["source_id"])
            later = Run(
                thread_id=previous.thread_id,
                state="queued",
                selected_source_ids=[derived_source.id],
                config={
                    "answer_language": "en-IN",
                    "source_versions": {derived_source.id: derived_source.version},
                    "selected_dataset_ids": reused.data["dataset_ids"],
                },
            )
            session.add(later)
            session.flush()
            session.add(
                Job(
                    kind="agent_run",
                    run_id=later.id,
                    dedupe_key=str(uuid4()),
                    payload={},
                )
            )
            later_id = later.id
        with db_factory() as session, session.begin():
            later_claim = claim_job(session, "phase06-later", 60)
        assert later_claim is not None
        later_runtime = RunRuntime(later_claim, settings, db_factory)
        try:
            later_result = await later_runtime.dispatch(
                "analyze_data",
                call("analyze_data"),
                AnalyzeInput(
                    inputs=[
                        {
                            "alias": "regional",
                            "dataset_id": reused.data["dataset_ids"][0],
                        }
                    ],
                    result_frame="regional",
                ),
            )
            assert later_result.status == "ok", later_result.model_dump()
            assert later_result.data["analysis"]["rows"][0]["total_inr"] == "25000.00"
            later_output = next(
                a
                for a in later_result.data["artifacts"]
                if a["display_name"] == "result.csv"
            )
            with db_factory() as session:
                artifact = session.get(Artifact, later_output["id"])
                assert f"artifact:{output['id']}" in artifact.lineage
                assert f"source:{db_source_id}@1" in artifact.lineage
        finally:
            if later_runtime.python:
                await later_runtime.python.aclose()
            if later_runtime.sandbox:
                await later_runtime.sandbox.aclose()
        with engine.connect() as connection:
            after = list(
                connection.execute(
                    text(
                        "SELECT application_id,grant_amount_inr FROM public.synthetic_applications ORDER BY application_id"
                    )
                )
            )
        assert after == before
        proof = {
            "status": "passed",
            "database_rows_unchanged": True,
            "original_hash_unchanged": True,
            "original_sha256": original.sha256,
            "query_snapshot_sha256": snapshot["sha256"],
            "result_sha256": output["sha256"],
            "analysis": result.data["analysis"],
            "staged_input_artifacts": result.data["staged_input_artifacts"],
            "derived_dataset_ids": reused.data["dataset_ids"],
            "later_run_reuse": True,
        }
        Path("../evals/results/phase06-mixed-live.json").write_text(
            json.dumps(proof, indent=2)
        )
    finally:
        if runtime.python:
            await runtime.python.aclose()
        if runtime.sandbox:
            await runtime.sandbox.aclose()
        engine.dispose()
