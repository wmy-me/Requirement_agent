from __future__ import annotations

import time
import threading

import requirement_agent.tools.base as base_module
from requirement_agent.tools.base import BaseTool, ToolInput, ToolResult, ToolStatus


class _Input(ToolInput):
    value: str = "ok"


class _SlowTool(BaseTool):
    name = "budget_test"
    description = "test only"
    input_model = _Input
    timeout_seconds = 2.0

    def execute(self, params: _Input) -> ToolResult:
        time.sleep(0.15)
        return ToolResult.success({"value": params.value})


def test_remaining_task_budget_caps_tool_wait() -> None:
    started = time.monotonic()
    result = _SlowTool().run({"value": "x"}, timeout_seconds=0.03)
    assert result.status is ToolStatus.ERROR
    assert "超过 0.03s" in (result.message or "")
    assert time.monotonic() - started < 0.12


def test_exhausted_task_budget_does_not_execute_tool() -> None:
    result = _SlowTool().run({"value": "x"}, timeout_seconds=0)
    assert result.status is ToolStatus.ERROR
    assert result.message == "任务预算已耗尽，未执行工具"


def test_saturated_queue_consumes_same_deadline_and_never_runs_late(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class BlockingTool(_SlowTool):
        def execute(self, params: _Input) -> ToolResult:
            calls.append(params.value)
            started.set()
            release.wait(1)
            return ToolResult.success({"value": params.value})

    monkeypatch.setattr(base_module, "_TOOL_SLOTS", threading.BoundedSemaphore(1))
    first = threading.Thread(target=lambda: BlockingTool().run({"value": "first"}, timeout_seconds=1))
    first.start()
    assert started.wait(0.2)
    began = time.monotonic()
    late = BlockingTool().run({"value": "late"}, timeout_seconds=0.04)
    assert late.status is ToolStatus.ERROR
    assert time.monotonic() - began < 0.12
    release.set()
    first.join(1)
    # The rejected queued call never reaches execute after capacity returns.
    assert calls == ["first"]
