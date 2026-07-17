"""Node 6 contracts for durable, serial and cancellable Session attempts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.session.events import EventBus
from src.session.models import AttemptStatus
from src.session.service import SessionService
from src.session.store import SessionStore
from src.state.jobs import JobStatus, SQLiteJobQueue


class _DummyIndex:
    def index_session(self, session_id: str, title: str) -> None:
        del session_id, title

    def index_message(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content


def _service(tmp_path: Path, *, worker_count: int = 4) -> SessionService:
    store = SessionStore(tmp_path / "sessions")
    service = SessionService(
        store,
        EventBus(event_store=store),
        tmp_path / "runs",
        worker_count=worker_count,
        lease_seconds=2,
        heartbeat_interval=0.1,
        attempt_timeout_seconds=5,
        retry_delay_seconds=0.01,
    )
    service._search_index = _DummyIndex()
    return service


def test_duplicate_message_key_returns_original_without_duplicate_attempt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        session = service.create_session("idempotent")

        async def run_agent(*args, **kwargs):
            del args, kwargs
            return {"status": "success", "content": "done"}

        service._run_with_agent = run_agent
        first = await service.send_message(
            session.session_id, "research NVDA", idempotency_key="request-0001"
        )
        duplicate = await service.send_message(
            session.session_id, "MUST NOT REPLACE", idempotency_key="request-0001"
        )
        await service.wait_for_idle(timeout=5)

        assert duplicate == first
        messages = service.get_messages(session.session_id)
        assert [message.content for message in messages if message.role == "user"] == [
            "research NVDA"
        ]
        with service.store.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE session_id=?", (session.session_id,)
            ).fetchone()[0]
        assert count == 1
        assert len(service.job_queue.list(service.QUEUE_NAME)) == 1
        await service.shutdown()

    asyncio.run(scenario())


def test_timeout_is_retried_then_exits_with_a_classified_terminal_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.attempt_timeout_seconds = 0.02
        session = service.create_session("timeout")

        async def run_agent(*args, **kwargs):
            del args, kwargs
            await asyncio.sleep(1)
            return {"status": "success", "content": "too late"}

        service._run_with_agent = run_agent
        response = await service.send_message(session.session_id, "slow LLM")
        await service.wait_for_idle(timeout=3)

        attempt = service.store.get_attempt(session.session_id, response["attempt_id"])
        job = service.job_queue.list(service.QUEUE_NAME)[0]
        assert attempt.status == AttemptStatus.FAILED
        assert job.status == JobStatus.FAILED
        assert job.attempts == 2
        assert job.error_type == "llm_timeout"
        await service.shutdown()

    asyncio.run(scenario())


def test_tool_timeout_is_classified_separately_from_llm_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        session = service.create_session("tool-timeout")

        async def run_agent(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("tool timeout while reading provider")

        service._run_with_agent = run_agent
        await service.send_message(session.session_id, "read data")
        await service.wait_for_idle(timeout=3)

        job = service.job_queue.list(service.QUEUE_NAME)[0]
        assert job.status == JobStatus.FAILED
        assert job.error_type == "tool_timeout"
        await service.shutdown()

    asyncio.run(scenario())


def test_restart_recovers_expired_running_attempt_without_zombie(tmp_path: Path) -> None:
    class Clock:
        value = 1000.0

        def __call__(self) -> float:
            return self.value

    async def scenario() -> None:
        clock = Clock()
        store = SessionStore(tmp_path / "sessions")
        queue = SQLiteJobQueue(store.database, now_fn=clock)
        service_one = SessionService(
            store,
            EventBus(event_store=store),
            tmp_path / "runs",
            job_queue=queue,
            lease_seconds=2,
            heartbeat_interval=0.1,
            attempt_timeout_seconds=5,
        )
        service_one._search_index = _DummyIndex()
        session = service_one.create_session("recovery")
        started = asyncio.Event()

        async def interrupted_run(*args, **kwargs):
            del args, kwargs
            started.set()
            await asyncio.Event().wait()

        service_one._run_with_agent = interrupted_run
        response = await service_one.send_message(session.session_id, "recover me")
        await asyncio.wait_for(started.wait(), timeout=2)
        await service_one.shutdown()

        clock.value += 3
        recovered_store = SessionStore(tmp_path / "sessions")
        service_two = SessionService(
            recovered_store,
            EventBus(event_store=recovered_store),
            tmp_path / "runs",
            job_queue=SQLiteJobQueue(recovered_store.database, now_fn=clock),
            lease_seconds=2,
            heartbeat_interval=0.1,
            attempt_timeout_seconds=5,
        )
        service_two._search_index = _DummyIndex()

        async def recovered_run(*args, **kwargs):
            del args, kwargs
            return {"status": "success", "content": "recovered"}

        service_two._run_with_agent = recovered_run
        service_two.start()
        await service_two.wait_for_idle(timeout=3)

        attempt = recovered_store.get_attempt(session.session_id, response["attempt_id"])
        job = service_two.job_queue.list(service_two.QUEUE_NAME)[0]
        assert attempt.status == AttemptStatus.COMPLETED
        assert job.status == JobStatus.COMPLETED
        assert job.attempts == 2
        await service_two.shutdown()

    asyncio.run(scenario())


def test_same_session_is_serial_while_other_session_runs_concurrently(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        session_a = service.create_session("A")
        session_b = service.create_session("B")
        a1_started = asyncio.Event()
        b1_started = asyncio.Event()
        release_a1 = asyncio.Event()
        starts: list[str] = []

        async def run_agent(attempt, *args, **kwargs):
            del args, kwargs
            starts.append(attempt.prompt)
            if attempt.prompt == "a1":
                a1_started.set()
                await release_a1.wait()
            elif attempt.prompt == "b1":
                b1_started.set()
            return {"status": "success", "content": attempt.prompt}

        service._run_with_agent = run_agent
        await service.send_message(session_a.session_id, "a1")
        await service.send_message(session_a.session_id, "a2")
        await service.send_message(session_b.session_id, "b1")

        await asyncio.wait_for(a1_started.wait(), timeout=2)
        await asyncio.wait_for(b1_started.wait(), timeout=2)
        assert "a2" not in starts

        release_a1.set()
        await service.wait_for_idle(timeout=5)
        assert starts.index("a1") < starts.index("a2")
        assert {job.status for job in service.job_queue.list(service.QUEUE_NAME)} == {
            JobStatus.COMPLETED
        }
        await service.shutdown()

    asyncio.run(scenario())


def test_cancelled_attempt_cannot_append_late_assistant_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        session = service.create_session("cancel")
        started = asyncio.Event()
        release = asyncio.Event()

        async def run_agent(*args, **kwargs):
            del args, kwargs
            started.set()
            await release.wait()
            return {"status": "success", "content": "late side effect"}

        service._run_with_agent = run_agent
        response = await service.send_message(session.session_id, "long research")
        await asyncio.wait_for(started.wait(), timeout=2)
        assert service.cancel_current(session.session_id) is True
        release.set()
        await service.wait_for_idle(timeout=5)

        attempt = service.store.get_attempt(session.session_id, response["attempt_id"])
        assert attempt.status == AttemptStatus.CANCELLED
        assert [message.role for message in service.get_messages(session.session_id)] == ["user"]
        job = service.job_queue.list(service.QUEUE_NAME)[0]
        assert job.status == JobStatus.CANCELLED
        assert job.result is None
        await service.shutdown()

    asyncio.run(scenario())
