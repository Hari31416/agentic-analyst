import asyncio
import logging
import signal
import socket
from uuid import uuid4

from app.audit.redaction import configure_logging
from app.config import get_settings
from app.db.models import Artifact
from app.db.session import factory
from app.storage.factory import get_storage
from app.workers.queue import Claim, LeaseLost, claim, fail, finish

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
                result = await asyncio.to_thread(maintenance, task)
                with factory()() as session, session.begin():
                    finish(session, task.id, task.token, result)
        except LeaseLost:
            logger.warning("worker lease lost")
        except Exception:
            logger.error("worker task failed")
            if task:
                try:
                    with factory()() as session, session.begin():
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


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
