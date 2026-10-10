"""Shared lifecycle primitives for resumable Agent executions.

It deliberately does not own business analysis or approvals: callers provide a
pure runner and this service owns only run creation, atomic claiming, durable
checkpoint/event state, and conditional completion.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from requirement_agent.infrastructure.db.repositories.agent_run import AgentRunRepository


class AgentExecutionService:
    def __init__(self, repo: AgentRunRepository | None = None) -> None:
        self.repo = repo or AgentRunRepository()

    def start(self, *, task_type: str, source_id: int | None = None, conversation_id: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, object]:
        values = {"task_type": task_type, "execution_version": "execution_v2", **(meta or {})}
        return self.repo.create_run(run_type="analysis", source_id=source_id, conversation_id=conversation_id, meta=values)

    def claim(self, run_id: str) -> dict[str, object] | None:
        return self.repo.claim_run(run_id)

    def checkpoint(self, run_id: str, token: str, state: dict[str, Any], events: list[dict[str, Any]] | None = None) -> bool:
        # Reuse the event repository's ordered append; checkpoint persistence is
        # intentionally explicit so callers cannot accidentally reset budgets.
        if events and not self.repo.append_events_if_owned(run_id, token, events):
            return False
        return self.repo.checkpoint_if_owned(run_id, token, state)

    def events(self, run_id: str, token: str, events: list[dict[str, Any]]) -> bool:
        return bool(self.repo.append_events_if_owned(run_id, token, events))

    def tool_calls(self, run_id: str, token: str, records: list[dict[str, Any]]) -> bool:
        if not records:
            # Probe ownership: empty is a valid completed tool batch.
            return self.repo.heartbeat(run_id, token)
        return self.repo.record_tool_invocations_if_owned(run_id, token, records) == len(records)

    def heartbeat(self, run_id: str, token: str) -> bool:
        return self.repo.heartbeat(run_id, token)

    def complete(self, run_id: str, token: str, *, current_node: str | None = None) -> bool:
        return self.repo.finish_with_event_if_owned(run_id, token, status="completed", current_node=current_node, event={"event_type": "run_completed", "node": current_node, "payload": {}})

    def fail(self, run_id: str, token: str, error: BaseException, *, current_node: str | None = None) -> bool:
        return self.repo.finish_with_event_if_owned(run_id, token, status="failed", error=str(error), current_node=current_node, event={"event_type": "run_failed", "node": current_node, "payload": {"error": str(error)}})


__all__ = ["AgentExecutionService"]
