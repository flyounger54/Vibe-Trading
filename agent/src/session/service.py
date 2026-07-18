"""Session lifecycle orchestration for message flow, attempt creation, and execution scheduling.

V5: Uses AgentLoop instead of the fixed pipeline behind the generate skill.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from pathlib import Path
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.session.events import EventBus
from src.session.manifest import evaluate_quality_gate, write_run_manifest
from src.session.models import (
    Attempt,
    AttemptStatus,
    Message,
    Session,
)
from src.session.search import get_shared_index
from src.session.store import SessionStore
from src.state.jobs import JobRecord, JobStatus, SQLiteJobQueue

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop


# Dedicated thread pool limited to four concurrent agents to avoid exhausting the default executor.
_AGENT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
logger = logging.getLogger(__name__)


class SessionService:
    """Session lifecycle service.

    Attributes:
        store: Session persistence store.
        event_bus: SSE event bus.
        runs_dir: Root runs directory.
    """

    QUEUE_NAME = "session_attempts"

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
        runs_dir: Path,
        *,
        job_queue: SQLiteJobQueue | None = None,
        worker_count: int = 4,
        lease_seconds: float = 30,
        heartbeat_interval: float = 10,
        attempt_timeout_seconds: float = 900,
        max_attempts: int = 2,
        retry_delay_seconds: float = 1,
    ) -> None:
        """Initialize the session service.

        Args:
            store: Session persistence store.
            event_bus: SSE event bus.
            runs_dir: Root runs directory.
        """
        self.store = store
        self.event_bus = event_bus
        self.runs_dir = runs_dir
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if min(lease_seconds, heartbeat_interval, attempt_timeout_seconds) <= 0:
            raise ValueError("lease, heartbeat and attempt timeout must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if heartbeat_interval >= lease_seconds:
            raise ValueError("heartbeat_interval must be shorter than lease_seconds")
        self.job_queue = job_queue or SQLiteJobQueue(store.database)
        self.worker_count = worker_count
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._worker_prefix = f"session-{uuid.uuid4().hex[:12]}"
        self._worker_tasks: set[asyncio.Task[None]] = set()
        self._executing_jobs: set[str] = set()
        self._stopping = False
        self._active_loops: Dict[str, "AgentLoop"] = {}
        self._search_index = get_shared_index()

    def create_session(self, title: str = "", config: Optional[Dict[str, Any]] = None) -> Session:
        """Create a new session.

        Args:
            title: Session title.
            config: Session configuration.

        Returns:
            The newly created Session.
        """
        session = Session(title=title, config=config or {})
        self.store.create_session(session)
        self._search_index.index_session(session.session_id, title)
        self.event_bus.emit(session.session_id, "session.created", {"session_id": session.session_id, "title": title})
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Return a session by ID."""
        return self.store.get_session(session_id)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """List all sessions."""
        return self.store.list_sessions(limit)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        self.event_bus.clear(session_id)
        return self.store.delete_session(session_id)

    async def send_message(
        self,
        session_id: str,
        content: str,
        role: str = "user",
        *,
        include_shell_tools: bool = False,
        idempotency_key: str | None = None,
    ) -> Dict[str, Any]:
        """Send a message to a session and trigger execution.

        Args:
            session_id: Session ID.
            content: Message content.
            role: Message role.
            include_shell_tools: Whether this attempt may use shell tools.

        Returns:
            Dictionary containing message_id and attempt_id.
        """
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if role != "user":
            message = Message(session_id=session_id, role=role, content=content)
            self.store.append_message(message)
            self._search_index.index_message(session_id, role, content)
            self.event_bus.emit(
                session_id,
                "message.received",
                {"message_id": message.message_id, "role": role, "content": content},
            )
            return {"message_id": message.message_id}

        message = Message(session_id=session_id, role=role, content=content)
        attempt = Attempt(session_id=session_id, parent_attempt_id=session.last_attempt_id, prompt=content)
        durable_key = f"{session_id}:{idempotency_key}" if idempotency_key else None
        job = self.job_queue.enqueue(
            self.QUEUE_NAME,
            {
                "session_id": session_id,
                "message": message.to_dict(),
                "attempt": attempt.to_dict(),
                "include_shell_tools": include_shell_tools,
            },
            idempotency_key=durable_key,
            concurrency_key=session_id,
            max_attempts=self.max_attempts,
        )
        stored_message, stored_attempt, message_created, attempt_created = self._materialize_job(job)
        if message_created:
            self._search_index.index_message(session_id, role, stored_message.content)
            self.event_bus.emit(
                session_id,
                "message.received",
                {
                    "message_id": stored_message.message_id,
                    "role": stored_message.role,
                    "content": stored_message.content,
                },
            )
        if attempt_created:
            self.event_bus.emit(
                session_id,
                "attempt.created",
                {"attempt_id": stored_attempt.attempt_id, "prompt": stored_attempt.prompt},
            )

        self.start()
        return {
            "message_id": stored_message.message_id,
            "attempt_id": stored_attempt.attempt_id,
        }

    def get_messages(self, session_id: str, limit: int = 100) -> list[Message]:
        """Return the message history."""
        return self.store.get_messages(session_id, limit)

    def cancel_current(self, session_id: str) -> bool:
        """Cancel the currently running AgentLoop for a session.

        Args:
            session_id: Session ID.

        Returns:
            Whether cancellation succeeded. True means an active loop existed and received a cancel signal.
        """
        job = self.job_queue.active_for_concurrency(self.QUEUE_NAME, session_id)
        if job is None or not self.job_queue.cancel(job.job_id, reason="user_cancelled"):
            return False
        attempt = self.store.get_attempt(session_id, job.payload["attempt"]["attempt_id"])
        if attempt is not None and attempt.status not in {
            AttemptStatus.COMPLETED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
        }:
            attempt.mark_cancelled("user_cancelled")
            self.store.finalize_attempt(attempt)
            self.event_bus.emit(
                session_id,
                "attempt.cancelled",
                {"attempt_id": attempt.attempt_id, "reason": "user_cancelled"},
            )
        loop = self._active_loops.get(session_id)
        if loop is not None:
            loop.cancel()
        return True

    def start(self) -> None:
        """Recover expired work and ensure durable queue workers are running."""
        self._stopping = False
        self.job_queue.recover_expired(self.QUEUE_NAME)
        self._reconcile_terminal_jobs()
        self._ensure_workers()

    async def shutdown(self) -> None:
        """Stop local workers; their leases make interrupted jobs recoverable."""
        self._stopping = True
        for loop in list(self._active_loops.values()):
            loop.cancel()
        tasks = list(self._worker_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker_tasks.clear()

    async def wait_for_idle(self, *, timeout: float = 30) -> None:
        """Wait until this service has no queued or executing Session job."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self.job_queue.has_unfinished(self.QUEUE_NAME) or self._executing_jobs:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Session runtime did not become idle")
            await asyncio.sleep(0.02)

    def readiness_snapshot(self) -> dict[str, Any]:
        """Return database, queue and on-demand worker readiness state."""
        database = self.store.database
        schema_version = database.schema_version()
        expected_schema_version = database.expected_schema_version
        integrity = database.integrity_check()
        live_workers = sum(not task.done() for task in self._worker_tasks)
        queue_counts = self.job_queue.status_counts(self.QUEUE_NAME)
        ready = bool(
            not self._stopping
            and self.worker_count > 0
            and integrity == "ok"
            and schema_version == expected_schema_version
        )
        return {
            "ready": ready,
            "database": {
                "integrity": integrity,
                "schema_version": schema_version,
                "expected_schema_version": expected_schema_version,
            },
            "workers": {
                "mode": "on_demand",
                "configured": self.worker_count,
                "live": live_workers,
                "executing": len(self._executing_jobs),
                "stopping": self._stopping,
            },
            "queue": {"name": self.QUEUE_NAME, "counts": queue_counts},
        }

    def _ensure_workers(self) -> None:
        if self._stopping:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._worker_tasks = {task for task in self._worker_tasks if not task.done()}
        for index in range(len(self._worker_tasks), self.worker_count):
            worker_id = f"{self._worker_prefix}-{index}-{uuid.uuid4().hex[:8]}"
            task = asyncio.create_task(self._worker_loop(worker_id))
            self._worker_tasks.add(task)
            task.add_done_callback(self._worker_tasks.discard)

    async def _worker_loop(self, worker_id: str) -> None:
        while not self._stopping:
            job = self.job_queue.claim(
                self.QUEUE_NAME,
                worker_id,
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                if not self.job_queue.has_unfinished(self.QUEUE_NAME):
                    return
                await asyncio.sleep(min(0.1, self.heartbeat_interval))
                continue
            self._executing_jobs.add(job.job_id)
            try:
                await self._run_attempt_job(job, worker_id)
            finally:
                self._executing_jobs.discard(job.job_id)

    async def _run_attempt_job(self, job: JobRecord, worker_id: str) -> None:
        """Execute one claimed job and fence all terminal writes by its lease."""
        session_id = str(job.payload["session_id"])
        logger.info(
            "job_started",
            extra={"job_id": job.job_id, "session_id": session_id},
        )
        _, attempt, _, _ = self._materialize_job(job)
        session = self.store.get_session(session_id)
        if session is None:
            self.job_queue.fail(
                job.job_id,
                worker_id,
                f"Session {session_id} not found",
                error_type="missing_session",
            )
            return
        attempt.mark_running()
        self.store.update_attempt(attempt)
        self.event_bus.emit(session_id, "attempt.started", {"attempt_id": attempt.attempt_id})

        heartbeat = asyncio.create_task(self._heartbeat(job.job_id, worker_id))

        try:
            messages = self.store.get_messages(session_id)
            result = await asyncio.wait_for(
                self._run_with_agent(
                    attempt,
                    messages=messages,
                    include_shell_tools=bool(job.payload.get("include_shell_tools", False)),
                    session_config=dict(session.config),
                    job_id=job.job_id,
                    worker_id=worker_id,
                ),
                timeout=self.attempt_timeout_seconds,
            )
            logger.info(
                "job_execution_finished",
                extra={"job_id": job.job_id, "session_id": session_id},
            )
            result = self._attach_manifest_and_quality_gate(job, attempt, result)
            if not self.job_queue.complete(job.job_id, worker_id, result):
                self._mark_cancelled_if_needed(job, attempt, worker_id)
                return
            self._record_result(session_id, attempt, result)
        except asyncio.TimeoutError:
            loop = self._active_loops.get(session_id)
            if loop is not None:
                loop.cancel()
            self._record_failure(
                job,
                attempt,
                worker_id,
                "LLM attempt timed out",
                error_type="llm_timeout",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_failure(
                job,
                attempt,
                worker_id,
                str(exc),
                error_type=self._classify_error(exc),
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, job_id: str, worker_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            if not self.job_queue.heartbeat(
                job_id, worker_id, lease_seconds=self.lease_seconds
            ):
                return

    def _materialize_job(
        self, job: JobRecord
    ) -> tuple[Message, Attempt, bool, bool]:
        """Recreate queue payload records safely after a process interruption."""
        message = Message.from_dict(dict(job.payload["message"]))
        attempt = Attempt.from_dict(dict(job.payload["attempt"]))
        message_created, attempt_created = self.store.ensure_queued_attempt(
            message,
            attempt,
            include_shell_tools=bool(job.payload.get("include_shell_tools", False)),
        )
        return message, attempt, message_created, attempt_created

    def _attach_manifest_and_quality_gate(
        self,
        job: JobRecord,
        attempt: Attempt,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attach audit metadata and let deterministic checks reject false success."""
        normalized = dict(result)
        gate = evaluate_quality_gate(normalized)
        if normalized.get("status") == "success" and not gate["accepted"]:
            normalized.update(
                {
                    "status": "failed",
                    "error_code": "quality_gate_failed",
                    "reason": "deterministic quality gate rejected the claimed success",
                }
            )
        path, manifest = write_run_manifest(
            root_dir=self.runs_dir,
            session_id=attempt.session_id,
            attempt_id=attempt.attempt_id,
            job_id=job.job_id,
            prompt=attempt.prompt,
            result=normalized,
            error_type=normalized.get("error_code"),
        )
        normalized["manifest_path"] = str(path)
        normalized["quality_gate"] = manifest["quality_gate"]
        return normalized

    def _record_result(
        self, session_id: str, attempt: Attempt, result: Dict[str, Any]) -> None:
        """Persist the winner's terminal result and a deduplicated assistant reply."""
        if result.get("status") == "success":
            attempt.mark_completed(summary=result.get("content", ""))
        else:
            attempt.mark_failed(error=result.get("reason", "unknown"))
        attempt.run_dir = result.get("run_dir")
        trace = result.get("react_trace")
        if isinstance(trace, list):
            attempt.react_trace = trace
        metrics = result.get("metrics")
        if isinstance(metrics, dict):
            attempt.metrics = metrics
        reply_metadata: dict[str, Any] = {"status": attempt.status.value}
        if attempt.run_dir:
            reply_metadata["run_id"] = Path(attempt.run_dir).name
        if attempt.metrics:
            reply_metadata["metrics"] = attempt.metrics
        reply = Message(
            message_id=f"assistant-{attempt.attempt_id}",
            session_id=session_id,
            role="assistant",
            content=self._format_result_message(attempt),
            linked_attempt_id=attempt.attempt_id,
            metadata=reply_metadata,
        )
        self.store.finalize_attempt(attempt, reply)
        self._search_index.index_message(session_id, "assistant", reply.content)
        self.event_bus.emit(
            session_id,
            "attempt.completed" if attempt.status == AttemptStatus.COMPLETED else "attempt.failed",
            {
                "attempt_id": attempt.attempt_id,
                "status": attempt.status.value,
                "summary": attempt.summary,
                "error": attempt.error,
                "run_dir": attempt.run_dir,
            },
        )

    def _record_failure(
        self,
        job: JobRecord,
        attempt: Attempt,
        worker_id: str,
        error: str,
        *,
        error_type: str,
    ) -> None:
        status = self.job_queue.fail(
            job.job_id,
            worker_id,
            error,
            error_type=error_type,
            retry_delay=self.retry_delay_seconds,
        )
        if status == JobStatus.RETRY_WAIT:
            attempt.status = AttemptStatus.PENDING
            attempt.completed_at = None
            attempt.error = error
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                attempt.session_id,
                "attempt.retrying",
                {
                    "attempt_id": attempt.attempt_id,
                    "error": error,
                    "error_type": error_type,
                },
            )
            return
        if status == JobStatus.CANCELLED:
            self._mark_cancelled_if_needed(job, attempt, worker_id)
            return
        if status == JobStatus.FAILED:
            attempt.mark_failed(error)
            self._record_terminal_attempt_failure(attempt, error_type)

    def _record_terminal_attempt_failure(self, attempt: Attempt, error_type: str) -> None:
        reply = Message(
            message_id=f"assistant-{attempt.attempt_id}",
            session_id=attempt.session_id,
            role="assistant",
            content=self._format_result_message(attempt),
            linked_attempt_id=attempt.attempt_id,
            metadata={"status": attempt.status.value, "error_type": error_type},
        )
        self.store.finalize_attempt(attempt, reply)
        self._search_index.index_message(attempt.session_id, "assistant", reply.content)
        self.event_bus.emit(
            attempt.session_id,
            "attempt.failed",
            {
                "attempt_id": attempt.attempt_id,
                "status": attempt.status.value,
                "error": attempt.error,
                "error_type": error_type,
            },
        )

    def _mark_cancelled_if_needed(
        self, job: JobRecord, attempt: Attempt, worker_id: str
    ) -> None:
        if self.job_queue.is_cancelled(job.job_id) and attempt.status != AttemptStatus.CANCELLED:
            attempt.mark_cancelled("user_cancelled")
            self.store.finalize_attempt(attempt)
            self.event_bus.emit(
                attempt.session_id,
                "attempt.cancelled",
                {"attempt_id": attempt.attempt_id, "reason": "user_cancelled"},
            )
        self.job_queue.release_cancelled(job.job_id, worker_id)

    def _reconcile_terminal_jobs(self) -> None:
        """Finish durable writes that may have been interrupted after queue CAS."""
        for job in self.job_queue.list(self.QUEUE_NAME):
            if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                continue
            try:
                _, attempt, _, _ = self._materialize_job(job)
            except (KeyError, TypeError, ValueError):
                continue
            if attempt.status in {
                AttemptStatus.COMPLETED,
                AttemptStatus.FAILED,
                AttemptStatus.CANCELLED,
            }:
                continue
            if job.status == JobStatus.COMPLETED:
                self._record_result(attempt.session_id, attempt, job.result or {})
            elif job.status == JobStatus.CANCELLED:
                attempt.mark_cancelled(job.error or "cancelled")
                self.store.finalize_attempt(attempt)
            else:
                attempt.mark_failed(job.error or "worker failed")
                self._record_terminal_attempt_failure(attempt, job.error_type or "worker_failure")

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message:
            return "tool_timeout" if "tool" in message else "llm_timeout"
        return "runtime_error"

    async def _run_with_agent(
        self,
        attempt: Attempt,
        messages: list = None,
        *,
        include_shell_tools: bool = False,
        session_config: Optional[Dict[str, Any]] = None,
        job_id: str | None = None,
        worker_id: str | None = None,
    ) -> Dict[str, Any]:
        """Execute an attempt with the V5 AgentLoop.

        Args:
            attempt: Current execution attempt.
            messages: Session message history.
            include_shell_tools: Whether the registry may include shell tools.
            session_config: Optional session-level config overrides. MCP server
                definitions under the ``mcpServers`` key are merged on top of
                the user config file via ``load_runtime_agent_config`` so each
                session can extend or override the global MCP server list.

        Returns:
            Result dictionary containing status, run_dir, run_id, metrics, and related fields.
        """
        from src.tools import build_registry
        from src.providers.chat import ChatLLM
        from src.agent.loop import AgentLoop
        from src.memory.persistent import PersistentMemory
        from src.config.loader import load_runtime_agent_config, sanitize_session_overrides

        llm = ChatLLM()
        pm = PersistentMemory()

        session_id = attempt.session_id
        attempt_id = attempt.attempt_id
        loop = asyncio.get_running_loop()

        safe_overrides = sanitize_session_overrides(session_config) if session_config else session_config
        agent_config = load_runtime_agent_config(overrides=safe_overrides)

        def event_callback(event_type: str, data: Dict[str, Any]) -> None:
            """Forward AgentLoop events to the SSE event bus."""
            if job_id and worker_id and not self.job_queue.is_owned(job_id, worker_id):
                return
            payload = dict(data)
            payload["attempt_id"] = attempt_id
            self.event_bus.emit(session_id, event_type, payload)

        def _mcp_collision_warn(msg: str) -> None:
            """Forward MCP server-name collision warnings to the operator event channel."""
            if job_id and worker_id and not self.job_queue.is_owned(job_id, worker_id):
                return
            self.event_bus.emit(session_id, "mcp.warning", {"attempt_id": attempt_id, "message": msg})

        registry = await loop.run_in_executor(
            _AGENT_EXECUTOR,
            lambda: build_registry(
                persistent_memory=pm,
                include_shell_tools=include_shell_tools,
                agent_config=agent_config,
                session_id=session_id,
                event_callback=event_callback,
                warn_callback=_mcp_collision_warn,
                tool_permission_check=(
                    lambda _tool_name, _params: (
                        (True, "")
                        if not job_id or not worker_id or self.job_queue.is_owned(job_id, worker_id)
                        else (False, "attempt lease is no longer active")
                    )
                ),
            ),
        )

        agent = AgentLoop(
            registry=registry,
            llm=llm,
            event_callback=event_callback,
            max_iterations=50,
            persistent_memory=pm,
        )
        self._active_loops[session_id] = agent

        # Build the message history context.
        history = self._convert_messages_to_history(messages) if messages else None

        try:
            result = await loop.run_in_executor(
                _AGENT_EXECUTOR,
                lambda: agent.run(
                    user_message=attempt.prompt,
                    history=history,
                    session_id=session_id,
                ),
            )
        finally:
            self._active_loops.pop(session_id, None)

        # Load metrics from the run output when available.
        if result.get("run_dir"):
            metrics = self._load_metrics(Path(result["run_dir"]))
            if metrics:
                result["metrics"] = metrics

        return result

    @staticmethod
    def _convert_messages_to_history(messages: list) -> list[Dict[str, Any]]:
        """Convert Session messages into OpenAI-format history.

        Keeps the readable ``[prev_run: {run_id}]`` marker instead of removing it
        completely, and trims by character budget instead of a hard six-message cap
        so the LLM can still see previous artifact paths and strategy content during
        iterative updates.

        Args:
            messages: Session message list without the current turn.

        Returns:
            OpenAI-format messages trimmed from the newest items within the token budget.
        """
        import re
        from pathlib import Path

        def _shorten_run_dir(match: re.Match) -> str:
            path_str = match.group(0).replace("Run directory:", "").strip()
            run_id = Path(path_str).name if path_str else ""
            return f"[prev_run: {run_id}]" if run_id else ""

        history = []
        for msg in messages[:-1]:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if not content.strip() or role not in ("user", "assistant"):
                continue
            content = re.sub(r"Run directory:\s*\S+", _shorten_run_dir, content).strip()
            if content:
                history.append({"role": role, "content": content})

        # Trim from the newest messages within a character budget of roughly 3000 tokens.
        MAX_HISTORY_CHARS = 12000
        total_chars = 0
        trimmed: list = []
        for msg in reversed(history):
            msg_len = len(msg.get("content", ""))
            if total_chars + msg_len > MAX_HISTORY_CHARS:
                break
            trimmed.append(msg)
            total_chars += msg_len
        return list(reversed(trimmed))

    @staticmethod
    def _load_metrics(run_dir: Path) -> Optional[Dict[str, Any]]:
        """Load metrics.csv from a run directory."""
        import csv
        metrics_path = run_dir / "artifacts" / "metrics.csv"
        if not metrics_path.exists():
            return None
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    return {k: float(v) for k, v in rows[0].items() if v}
        except Exception:
            pass
        return None

    @staticmethod
    def _format_result_message(attempt: Attempt) -> str:
        """Format the final execution result message."""
        if attempt.status == AttemptStatus.COMPLETED:
            return attempt.summary or "Strategy execution completed."
        return f"Execution failed: {attempt.error or 'unknown error'}"
