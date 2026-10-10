from __future__ import annotations

import threading
import time

from requirement_agent.application.agent_execution_service import ExecutionHeartbeat
from requirement_agent.application.execution_control import ExecutionStopped, bind_execution
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult


class _Service:
    def heartbeat(self, run_id: str, token: str) -> bool:
        raise OSError("database unavailable")


def test_heartbeat_database_exception_marks_executor_lost() -> None:
    heartbeat = ExecutionHeartbeat(_Service(), "run", "token", 0.01).start()
    try:
        deadline = time.monotonic() + 0.5
        while not heartbeat.lost and time.monotonic() < deadline:
            time.sleep(0.01)
        assert heartbeat.lost is True
    finally:
        heartbeat.stop()


class _Input(ToolInput):
    value: str = "x"


class _QueuedTool(BaseTool):
    name = "queued-boundary-test"
    description = "test"
    input_model = _Input

    def execute(self, params: _Input) -> ToolResult:
        return ToolResult.success({"ran": True})


def test_tool_rechecks_ownership_when_worker_starts() -> None:
    # The check is performed in the submitted worker, rather than solely at
    # admission time; an owner that is lost while queued cannot execute.
    with bind_execution(lambda: False, time.monotonic() + 1):
        result = _QueuedTool().run({})
    assert result.is_error
    assert "ExecutionStopped" in (result.message or "")
