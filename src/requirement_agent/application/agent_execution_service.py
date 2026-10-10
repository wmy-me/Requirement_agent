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
        if events:
            self.repo.append_events(run_id, events)
        return self.repo.checkpoint_if_owned(run_id, token, state)

    def heartbeat(self, run_id: str, token: str) -> bool:
        return self.repo.heartbeat(run_id, token)

    def complete(self, run_id: str, token: str, *, current_node: str | None = None) -> bool:
        return self.repo.finish_if_running(run_id, token=token, status="completed", current_node=current_node)

    def fail(self, run_id: str, token: str, error: BaseException, *, current_node: str | None = None) -> bool:
        return self.repo.finish_if_running(run_id, token=token, status="failed", error=str(error), current_node=current_node)


__all__ = ["AgentExecutionService"]
