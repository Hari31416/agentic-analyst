import asyncio
import logging
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from app.audit.redaction import configure_logging
from app.config import get_settings
from app.db.models import Artifact
from app.db.session import factory
from app.storage.factory import get_storage
from app.workers.queue import Claim, LeaseLost, claim, fail, finish, heartbeat
from app.agent.runtime import RunRuntime, RunCancelled

logger = logging.getLogger(__name__)


def maintenance(task: Claim) -> dict[str, object]:
    if task.kind != "verify_storage":
        raise ValueError("unknown maintenance task")
    storage = get_storage(get_settings())
    with factory()() as session:
        # The task receives an ID rather than a caller-controlled filesystem path.
        artifact = session.get(Artifact, task.payload.get("artifact_id"))
        if artifact is None:
            raise ValueError("artifact not found")
        valid = storage.verify(
            artifact.storage_key, artifact.sha256, artifact.byte_size
        )
    return {"artifact_id": artifact.id, "verified": valid}


async def run_worker() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    owner = f"{socket.gethostname()}-{uuid4()}"
    logger.info("worker started")
    while not stop.is_set():
        task: Claim | None = None
        try:
            with factory()() as session, session.begin():
                task = claim(session, owner, settings.job_lease_seconds)
            if task:
                result: dict[str, object]
                if task.kind == "agent_run":
                    await execute_run(task, stop)
                    result = {"run_id": task.run_id}
                else:
                    result = await asyncio.to_thread(maintenance, task)
                with factory()() as session, session.begin():
                    finish(session, task.id, task.token, result)
        except LeaseLost:
            logger.warning("worker lease lost")
        except RunCancelled:
            logger.info("run cancellation acknowledged")
            if task:
                try:
                    with factory()() as session, session.begin():
                        finish(
                            session,
                            task.id,
                            task.token,
                            {"run_id": task.run_id, "cancelled": True},
                        )
                except LeaseLost:
                    logger.warning("cancelled run lease lost before acknowledgement")
        except Exception:
            logger.error("worker task failed")
            if task:
                try:
                    with factory()() as session, session.begin():
                        if task.kind == "agent_run" and task.run_id:
                            runtime = RunRuntime(task, settings, factory())
                            run = runtime.guard(session, allow_cancelled=True)
                            if run.state not in {
                                "completed",
                                "awaiting_clarification",
                                "failed",
                                "cancelled",
                                "budget_exhausted",
                            }:
                                runtime._fail_open_tool_calls(
                                    session, run, "worker_failed"
                                )
                                runtime.finalize(
                                    session,
                                    run,
                                    "failed",
                                    {
                                        "partial": True,
                                        "error": {
                                            "code": "worker_failed",
                                            "message": "The run stopped unexpectedly. Retry as a new run.",
                                        },
                                    },
                                )
                        fail(
                            session,
                            task.id,
                            task.token,
                            "maintenance_failed",
                            retryable=False,
                        )
                except LeaseLost:
                    pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def execute_run(task: Claim, stop: asyncio.Event) -> None:
    settings = get_settings()
    runtime = RunRuntime(task, settings, factory())

    async def monitor() -> None:
        while True:
            # Poll cancellation promptly; refresh ownership before a third of the lease expires.
            with factory()() as session, session.begin():
                heartbeat(session, task.id, task.token, settings.job_lease_seconds)
                runtime.guard(session)
            if stop.is_set():
                raise RunCancelled("worker shutdown requested")
            await asyncio.sleep(1)

    execution = asyncio.create_task(runtime.run())
    observer = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait(
            [execution, observer], return_when=asyncio.FIRST_COMPLETED
        )
        if observer in done:
            execution.cancel()
            with suppress(asyncio.CancelledError, RunCancelled, LeaseLost):
                await execution
            try:
                observer.result()
            except RunCancelled:
                return
            observer.result()
        await execution
    finally:
        observer.cancel()
        with suppress(asyncio.CancelledError, RunCancelled, LeaseLost):
            await observer


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
