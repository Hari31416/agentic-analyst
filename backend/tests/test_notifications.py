import asyncio
from types import SimpleNamespace

import pytest

from app.db.notifications import Notifications, wait_for_wakeup


async def test_subscribe_routes_and_cleans_up():
    notifications = Notifications("sqlite://", "test")
    with (
        notifications.subscribe("one") as first,
        notifications.subscribe("one") as second,
        notifications.subscribe("two") as other,
    ):
        notifications.wake("one")
        assert first.is_set() and second.is_set() and not other.is_set()
        notifications.wake()
        assert other.is_set()
    assert notifications.subscribers == {}


async def test_hint_between_read_and_wait_is_retained():
    notifications = Notifications("sqlite://", "test")
    with notifications.subscribe() as event:
        event.clear()
        # Simulate a commit after the durable query but before waiting.
        notifications.wake("")
        await asyncio.wait_for(wait_for_wakeup(event, 60), 0.1)


async def test_polling_stop_and_cancel_leave_no_waiters():
    baseline = asyncio.all_tasks()
    await wait_for_wakeup(asyncio.Event(), 0.001)
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(wait_for_wakeup(asyncio.Event(), 60, stop), 0.1)
    task = asyncio.create_task(wait_for_wakeup(asyncio.Event(), 60))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert asyncio.all_tasks() == baseline


async def test_listener_reconnect_wakes_and_closes(monkeypatch):
    connections = []
    listening = asyncio.Queue()
    incoming = asyncio.Queue()

    class Connection:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def execute(self, query):
            if not isinstance(query, str):
                listening.put_nowait(self)

        async def notifies(self, **kwargs):
            value = await incoming.get()
            if isinstance(value, Exception):
                raise value
            yield SimpleNamespace(payload=value)

    async def connect(*args, **kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("app.db.notifications.psycopg.AsyncConnection.connect", connect)
    notifications = Notifications("postgresql://localhost/test", "test")
    with notifications.subscribe("run") as event:
        async with notifications:
            await asyncio.wait_for(listening.get(), 1)
            await asyncio.wait_for(event.wait(), 1)
            event.clear()
            incoming.put_nowait("run")
            await asyncio.wait_for(event.wait(), 1)
            event.clear()
            incoming.put_nowait(RuntimeError("disconnect"))
            await asyncio.wait_for(listening.get(), 2)
            await asyncio.wait_for(event.wait(), 1)
            assert connections[0].closed
        assert all(connection.closed for connection in connections)
        assert notifications.task is None


@pytest.mark.parametrize("notify", [True, False])
async def test_sse_rechecks_persisted_events_with_or_without_hint(monkeypatch, notify):
    from unittest.mock import AsyncMock
    import app.api.chat as chat

    notifications = Notifications("sqlite://", "test")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_notifications=notifications)),
        state=SimpleNamespace(),
        query_params={},
        is_disconnected=AsyncMock(return_value=False),
    )
    rows = []
    cursors = []

    def batch(run_id, after):
        cursors.append(after)
        return (
            [row for row in rows if row.sequence > after],
            "completed" if rows else "running",
            len(rows),
        )

    monkeypatch.setattr(chat, "event_batch", batch)
    response = await chat.stream_events("run", request, "4")
    stream = response.body_iterator
    assert await anext(stream) == ": heartbeat\n\n"
    rows.append(SimpleNamespace(sequence=5, model_dump_json=lambda: '{"sequence":5}'))
    if notify:
        notifications.wake("run")
    # The hint wakes immediately; without it the original polling bound still delivers.
    value = await asyncio.wait_for(anext(stream), 0.2 if notify else 1)
    assert value.startswith("id: 5\n")
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert cursors == [4, 4, 4]
    assert notifications.subscribers == {}


async def test_sse_cancellation_removes_subscription_and_waiters(monkeypatch):
    from unittest.mock import AsyncMock
    import app.api.chat as chat

    notifications = Notifications("sqlite://", "test")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_notifications=notifications)),
        state=SimpleNamespace(),
        query_params={},
        is_disconnected=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(chat, "event_batch", lambda *args: ([], "running", 0))
    response = await chat.stream_events("run", request, None)
    stream = response.body_iterator
    await anext(stream)
    baseline = asyncio.all_tasks()
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert notifications.subscribers == {}
    assert asyncio.all_tasks() == baseline


@pytest.mark.parametrize("notify", [True, False])
async def test_idle_worker_wakes_or_polls_and_closes(monkeypatch, notify):
    from unittest.mock import MagicMock
    import app.workers.main as worker

    notifications = Notifications("sqlite://", "test")
    handlers = []
    claims = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, handler: handlers.append(handler)
    )
    monkeypatch.setattr(worker, "Notifications", lambda *args: notifications)
    monkeypatch.setattr(worker, "factory", lambda: lambda: MagicMock())

    def claim(*args):
        claims.append(True)
        if len(claims) == 1 and notify:
            notifications.wake("")
        if len(claims) == 2:
            handlers[0]()
        return None

    async def wait(event, timeout, stop):
        await wait_for_wakeup(event, 60 if notify else 0.001, stop)

    monkeypatch.setattr(worker, "claim", claim)
    monkeypatch.setattr(worker, "wait_for_wakeup", wait)
    await asyncio.wait_for(worker.run_worker(), 0.5)
    assert len(claims) == 2
    assert notifications.task is None
    assert notifications.subscribers == {}
