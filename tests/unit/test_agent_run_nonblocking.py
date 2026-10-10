from __future__ import annotations

import asyncio
import threading
import time

from requirement_agent.api.routes import agent as agent_route
from requirement_agent.api.schemas import AgentRunRequest


class _Heartbeat:
    lost = False

    def stop(self) -> None:
        return None


class _Execution:
    def __init__(self) -> None:
        self.token = "00000000-0000-0000-0000-000000000001"

    def start(self, **kwargs):
        return {"run_id": "00000000-0000-0000-0000-000000000002"}

    def claim(self, run_id):
        return {"execution_token": self.token, "meta": {"deadline_at_epoch": time.time() + 10}}

    def checkpoint(self, *args, **kwargs): return True
    def events(self, *args, **kwargs): return True
    def tool_calls(self, *args, **kwargs): return True
    def complete(self, *args, **kwargs): return True
    def incomplete(self, *args, **kwargs): return True
    def fail(self, *args, **kwargs): return True
    def owns(self, *args, **kwargs): return True
    def heartbeat_loop(self, *args, **kwargs): return _Heartbeat()


def test_agent_run_moves_blocking_analysis_off_async_loop(monkeypatch) -> None:
    entered = threading.Event()
    monkeypatch.setattr(agent_route, "AgentExecutionService", _Execution)

    def slow_analysis(**kwargs):
        entered.set()
        time.sleep(0.15)
        return {"run_events": [], "tool_calls": [], "completed_steps": [], "extracted": {}, "candidates": [], "analysis": {}, "risk": {}}

    monkeypatch.setattr(agent_route, "run_analysis", slow_analysis)
    async def exercise() -> None:
        task = asyncio.create_task(agent_route.run_agent_pipeline(AgentRunRequest(original_text="新增一个导出功能")))
        for _ in range(20):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        # If the synchronous graph ran on the event loop this await would only
        # be reached after slow_analysis returned.
        await asyncio.sleep(0.01)
        assert task.done() is False
        assert (await task)["status"] == "ok"

    asyncio.run(exercise())
