"""Best-effort Postgres wakeups. Callers must always reread durable state."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)
JOBS_CHANNEL = "analyst_jobs"
EVENTS_CHANNEL = "analyst_run_events"


class Notifications:
    """One dedicated connection per process/channel, with local subscriber fanout."""

    def __init__(self, database_url: str, channel: str) -> None:
        self.database_url = database_url
        self.channel = channel
        self.subscribers: dict[str, set[asyncio.Event]] = {}
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Notifications":
        self.task = asyncio.create_task(self._listen())
        return self

    async def __aexit__(self, *args: object) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    @contextmanager
    def subscribe(self, key: str = "") -> Iterator[asyncio.Event]:
        event = asyncio.Event()
        subscribers = self.subscribers.setdefault(key, set())
        subscribers.add(event)
        try:
            yield event
        finally:
            subscribers.remove(event)
            if not subscribers:
                del self.subscribers[key]

    def wake(self, key: str | None = None) -> None:
        groups = (
            self.subscribers.values()
            if key is None
            else [self.subscribers.get(key, set())]
        )
        for subscribers in groups:
            for event in subscribers:
                event.set()

    async def _listen(self) -> None:
        url = make_url(self.database_url)
        if url.get_backend_name() != "postgresql":
            return
        # Preserve URL query options, including TLS settings, without logging credentials.
        conninfo = url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    conninfo, autocommit=True, connect_timeout=3
                ) as connection:
                    await connection.execute(
                        sql.SQL("LISTEN {}").format(sql.Identifier(self.channel))
                    )
                    # LISTEN is effective before this wake. Readers clear BEFORE querying,
                    # so a commit during subscription or a query cannot be lost.
                    self.wake()
                    # Periodically probe to detect silent disconnects. Polling by
                    # consumers continues independently while this task reconnects.
                    while True:
                        await connection.execute("SELECT 1")
                        notifications = connection.notifies(timeout=1)
                        try:
                            async for notification in notifications:
                                self.wake(notification.payload)
                        finally:
                            await notifications.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Postgres notification listener unavailable; polling continues"
                )
            await asyncio.sleep(1)


async def wait_for_wakeup(
    event: asyncio.Event, timeout: float, stop: asyncio.Event | None = None
) -> None:
    """Bound the hint wait and clean up waiters even on request/task cancellation."""
    waiters = [asyncio.create_task(event.wait())]
    if stop is not None:
        waiters.append(asyncio.create_task(stop.wait()))
    try:
        await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
