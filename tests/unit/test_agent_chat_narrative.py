"""对话叙事链路的回归测试。

背景：`_narrative_chunks` 里写成 `except queue.Empty`，但本模块是
`from queue import Queue` —— **没有导入 queue 模块**，而局部变量 `queue` 会遮蔽模块名。
于是第一次 `get_nowait()` 抛 `Empty`（执行器线程还没来得及产出，几乎立刻发生）时，
`except` 子句**自己**抛 AttributeError，被管线的 `except Exception: pass` 吞掉，
`narrative_parts` 为空 → 永远退回规则文案。

实测后果：库里 9 条 assistant 消息中 8 条是模板拼的，模型叙事一次都没成功过。
"""

import asyncio
import json

import httpx
import pytest

from requirement_agent.api.routes import agent_chat
from requirement_agent.infrastructure.llm.invocation import current_run_id, record_invocation, set_recorder


class FakeStreamProvider:
    """配置为真、流式返回固定片段的假 provider。"""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces

    def is_configured(self) -> bool:
        return True

    def generate_stream(self, prompt: str, system_prompt: str | None = None):
        yield from self.pieces


def _collect(pieces: list[str], monkeypatch) -> list[str]:
    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: FakeStreamProvider(pieces))
    pipeline = {
        "extracted": {"requirement_title": "登录增强"},
        "candidates": [],
        "analysis": {"duplicate": False, "related": False, "conflict": False},
        "risk": {"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low"},
    }

    async def run() -> list[str]:
        return [token async for token in agent_chat._narrative_chunks(pipeline)]

    return asyncio.run(run())


def test_streaming_narrative_is_forwarded(monkeypatch) -> None:
    """核心回归：模型流必须被逐段转发，而不是抛错后落到规则文案。"""
    assert _collect(["这是", "模型叙事"], monkeypatch) == ["这是", "模型叙事"]


def test_streaming_narrative_handles_empty_stream(monkeypatch) -> None:
    """模型一段都没给时要正常结束，不能挂死（也不能抛错）。"""
    assert _collect([], monkeypatch) == []


def test_question_answer_is_streamed_from_model(monkeypatch) -> None:
    """普通问答与需求叙事一样，必须逐段转发模型生成内容。"""
    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: FakeStreamProvider(["我是", "模型回答"]))

    async def run() -> list[str]:
        return [token async for token in agent_chat._answer_chunks("你是谁", run_id="answer-test")]

    assert asyncio.run(run()) == ["我是", "模型回答"]


def test_question_answer_uses_explicit_unavailable_message(monkeypatch) -> None:
    class UnconfiguredProvider(FakeStreamProvider):
        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: UnconfiguredProvider([]))

    async def run() -> list[str]:
        return [token async for token in agent_chat._answer_chunks("你是谁")]

    assert asyncio.run(run()) == [agent_chat.ANSWER_FALLBACK]


def test_narrative_invocation_keeps_run_id_inside_background_thread(monkeypatch) -> None:
    """手工 executor 线程不会自动继承 ContextVar，叙事必须显式重新绑定。"""
    records: list[dict[str, object]] = []

    class ObservedProvider(FakeStreamProvider):
        def __init__(self):
            super().__init__(["已记录"])
            self.task_type = ""

        def generate_stream(self, prompt: str, system_prompt: str | None = None):
            record_invocation({
                "run_id": current_run_id(), "task_type": self.task_type,
                "provider": "test", "model": "test", "status": "ok",
            })
            yield from self.pieces

    provider = ObservedProvider()
    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: provider)
    set_recorder(records.append)
    pipeline = {"extracted": {}, "candidates": [], "analysis": {}, "risk": {}}
    try:
        async def run() -> list[str]:
            return [token async for token in agent_chat._narrative_chunks(pipeline, run_id="run-audit-test")]

        assert asyncio.run(run()) == ["已记录"]
        assert records == [{
            "run_id": "run-audit-test", "task_type": "narrative",
            "provider": "test", "model": "test", "status": "ok",
        }]
    finally:
        set_recorder(None)


def test_casual_chat_is_not_requirement_intent() -> None:
    assert agent_chat._looks_like_casual_chat("你好你是谁")
    assert agent_chat._conversation_title_from_message("你好你是谁") == "了解需求助手"


def test_requirement_like_text_still_goes_to_pipeline() -> None:
    assert not agent_chat._looks_like_casual_chat("新增审批导出功能，支持按部门筛选")
    assert not agent_chat._looks_like_casual_chat("你好，帮我整理这条需求")


class _AssistantRunRepo:
    def append_events_if_owned(self, *_args, **_kwargs):
        return [{"sequence": 1}]


class _AssistantExecution:
    """只替换运行跟踪边界，验证外部 provider 异常的终态协议。"""

    failed: list[BaseException] = []

    def __init__(self) -> None:
        self.repo = _AssistantRunRepo()

    def claim(self, _run_id: str):
        return {"execution_token": "token", "meta": {"deadline_at_epoch": 4_102_444_800}}

    def heartbeat_loop(self, *_args):
        return type("Heartbeat", (), {"lost": False, "stop": lambda self: None, "mark_lost": lambda self: None})()

    def checkpoint(self, *_args, **_kwargs): return True
    def owns(self, *_args): return True
    def events(self, *_args): return True
    def tool_calls(self, *_args): return True

    def fail(self, _run_id: str, _token: str, error: BaseException, **_kwargs):
        self.failed.append(error)
        return True


class _AssistantChatRepo:
    def upsert_user_message(self, **_kwargs): return {"id": "user"}
    def create_run(self, **_kwargs): return {"run_id": "run-provider-failure"}


def _decode_sse(raw: str) -> tuple[str, dict[str, object]]:
    lines = dict(line.split(": ", 1) for line in raw.splitlines() if ": " in line)
    return lines["event"], json.loads(lines["data"])


def test_assistant_provider_network_error_is_persisted_and_has_explicit_terminal_frames(monkeypatch) -> None:
    """鉴权/网络/超时等 Exception 不能再让 SSE 静默截断。"""
    _AssistantExecution.failed = []
    monkeypatch.setattr(agent_chat, "AgentExecutionService", _AssistantExecution)
    monkeypatch.setattr(agent_chat, "chat_repo", _AssistantChatRepo())

    async def broken(*_args, **_kwargs):
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(agent_chat, "run_in_threadpool", broken)

    async def run():
        return [frame async for frame in agent_chat._stream_library_assistant(
            session_id="session", actor_id="actor", client_message_id="message",
            message="查询需求", history=[], allow_external=False, intent="ask_library",
        )]

    frames = [_decode_sse(frame) for frame in asyncio.run(run())]
    assert _AssistantExecution.failed and isinstance(_AssistantExecution.failed[0], httpx.ConnectError)
    assert frames[-2] == ("error", {"message": "执行失败：provider unavailable"})
    assert frames[-1] == ("done", {"run_id": "run-provider-failure", "status": "failed"})


def test_assistant_disconnect_cancellation_is_not_swallowed(monkeypatch) -> None:
    _AssistantExecution.failed = []
    monkeypatch.setattr(agent_chat, "AgentExecutionService", _AssistantExecution)
    monkeypatch.setattr(agent_chat, "chat_repo", _AssistantChatRepo())

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(agent_chat, "run_in_threadpool", cancelled)

    async def run():
        async for _frame in agent_chat._stream_library_assistant(
            session_id="session", actor_id="actor", client_message_id="message",
            message="查询需求", history=[], allow_external=False, intent="ask_library",
        ):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert not _AssistantExecution.failed
