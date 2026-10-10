"""Shared lifecycle primitives for resumable Agent executions.

It deliberately does not own business analysis or approvals: callers provide a
pure runner and this service owns only run creation, atomic claiming, durable
checkpoint/event state, and conditional completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.repositories.agent_run import AgentRunRepository


@dataclass(slots=True)
class ExecutionHeartbeat:
    """A bounded, cooperative lease renewer for one owned execution.

    A late owner may renew an expired lease *only while no one has reclaimed
    it*: the token predicate makes a successful takeover permanently visible to
    the old executor.  Python cannot force-stop a currently blocking model or
    database call; once it returns, ``lost`` prevents every subsequent call.
    """

    service: "AgentExecutionService"
    run_id: str
    token: str
    interval_seconds: float
    _stop: threading.Event = field(default_factory=threading.Event)
    _lost: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> "ExecutionHeartbeat":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"agent-lease-{self.run_id[:8]}", daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                renewed = self.service.heartbeat(self.run_id, self.token)
            except Exception:  # database outage means ownership is unknowable
                renewed = False
            if not renewed:
                self._lost.set()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds + 0.5))


class AgentExecutionService:
    def __init__(self, repo: AgentRunRepository | None = None) -> None:
        self.repo = repo or AgentRunRepository()

    def start(self, *, task_type: str, source_id: int | None = None, conversation_id: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, object]:
        values = {"task_type": task_type, "execution_version": "execution_v2", **(meta or {})}
        return self.repo.create_run(run_type="analysis", source_id=source_id, conversation_id=conversation_id, meta=values)

    @property
    def lease_seconds(self) -> float:
        return settings.agent_execution_lease_seconds

    @property
    def heartbeat_seconds(self) -> float:
        # A misconfigured interval must never be equal to/longer than lease.
        return min(settings.agent_execution_heartbeat_seconds, self.lease_seconds / 2)

    def claim(self, run_id: str) -> dict[str, object] | None:
        return self.repo.claim_run(run_id, lease_seconds=max(1, int(self.lease_seconds)))

    def checkpoint(self, run_id: str, token: str, state: dict[str, Any], events: list[dict[str, Any]] | None = None) -> bool:
        # Reuse the event repository's ordered append; checkpoint persistence is
        # intentionally explicit so callers cannot accidentally reset budgets.
        if events and not self.repo.append_events_if_owned(run_id, token, events):
            return False
        return self.repo.checkpoint_if_owned(run_id, token, state)

    def events(self, run_id: str, token: str, events: list[dict[str, Any]]) -> bool:
        saved = self.repo.append_events_if_owned(run_id, token, events)
        # [] is success iff the token check under the row lock succeeded.
        return bool(saved) if events else self.repo.owns_execution_locked(run_id, token)

    def tool_calls(self, run_id: str, token: str, records: list[dict[str, Any]]) -> bool:
        if not records:
            # Probe ownership: empty is a valid completed tool batch.
            return self.repo.owns_execution(run_id, token)
        return self.repo.record_tool_invocations_if_owned(run_id, token, records) == len(records)

    def heartbeat(self, run_id: str, token: str) -> bool:
        return self.repo.heartbeat(run_id, token, lease_seconds=max(1, int(self.lease_seconds)))

    def owns(self, run_id: str, token: str) -> bool:
        return self.repo.owns_execution(run_id, token)

    def heartbeat_loop(self, run_id: str, token: str) -> ExecutionHeartbeat:
        return ExecutionHeartbeat(self, run_id, token, self.heartbeat_seconds).start()

    def complete(self, run_id: str, token: str, *, current_node: str | None = None) -> bool:
        return self.repo.finish_with_event_if_owned(run_id, token, status="completed", current_node=current_node, event={"event_type": "run_completed", "node": current_node, "payload": {}})

    def fail(self, run_id: str, token: str, error: BaseException, *, current_node: str | None = None) -> bool:
        return self.repo.finish_with_event_if_owned(run_id, token, status="failed", error=str(error), current_node=current_node, event={"event_type": "run_failed", "node": current_node, "payload": {"error": str(error)}})

    def incomplete(self, run_id: str, token: str, reason: str, *, current_node: str | None = None) -> bool:
        """Persist budget exhaustion as an explicit non-success terminal result.

        ``agent_run`` has no ``incomplete`` database status; it is therefore a
        failed execution with a distinct final event and API result, never a
        silently completed analysis.
        """
        return self.repo.finish_with_event_if_owned(
            run_id, token, status="failed", error=reason, current_node=current_node,
            event={"event_type": "run_incomplete", "node": current_node, "payload": {"reason": reason}},
        )


__all__ = ["AgentExecutionService", "ExecutionHeartbeat"]
