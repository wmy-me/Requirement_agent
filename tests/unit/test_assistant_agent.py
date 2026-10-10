from __future__ import annotations

import time

from requirement_agent.agents.assistant_agent import RequirementAssistantAgent
from requirement_agent.application.execution_control import bind_execution
from requirement_agent.application.intent_service import IntentService
from requirement_agent.infrastructure.llm.openai_provider import ChatCompletion, ToolCall
from requirement_agent.tools.base import ToolResult


class _FakeToolModel:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, object]]] = []
        self.turn = 0

    def complete_with_tools(self, messages, _tools):
        self.messages.append(list(messages))
        self.turn += 1
        if self.turn == 1:
            return ChatCompletion("", (ToolCall("call-1", "list_requirements", '{"limit": 2}'),))
        if self.turn == 2:
            return ChatCompletion("", (ToolCall("call-2", "get_requirement_detail", '{"requirement_key":"REQ-1"}'),))
        return ChatCompletion("已找到 REQ-1。", ())


def test_react_agent_runs_multiple_structured_tool_rounds(monkeypatch) -> None:
    model = _FakeToolModel()
    seen: list[tuple[str, dict[str, object]]] = []
    budgets: list[float | None] = []

    def fake_invoke(name, params, *, consumer, remaining_seconds=None):
        assert consumer == "assistant"
        budgets.append(remaining_seconds)
        seen.append((name, params))
        if name == "list_requirements":
            return ToolResult.success([{"requirement_key": "REQ-1", "requirement_name": "登录"}])
        return ToolResult.success({"requirement_key": "REQ-1", "requirement_name": "登录", "final_requirement": "支持登录"})

    monkeypatch.setattr("requirement_agent.agents.assistant_agent.invoke", fake_invoke)
    monkeypatch.setattr(
        "requirement_agent.agents.assistant_agent.llm_schemas",
        lambda _consumer: [{"name": "list_requirements"}, {"name": "get_requirement_detail"}],
    )

    # 绑定任务 deadline 后，**每一次**工具调用都要拿到递减的剩余预算，
    # 而不是只有第一次（或只有初始化时的那一份）。
    with bind_execution(None, time.monotonic() + 30):
        result = RequirementAssistantAgent(model=model).answer("登录模块有哪些需求？")

    assert result.status == "completed"
    assert len(budgets) == 2 and all(b is not None and 0 < b <= 30 for b in budgets), budgets
    assert result.content == "已找到 REQ-1。"
    assert [item[0] for item in seen] == ["list_requirements", "get_requirement_detail"]
    assert [item["tool_call_id"] for item in result.tool_calls] == ["call-1", "call-2"]
    assert model.messages[1][-1]["tool_call_id"] == "call-1"
    assert result.sources()[0]["source_ref"] == "REQ-1"


def test_react_agent_returns_correctable_error_for_bad_tool_arguments(monkeypatch) -> None:
    class BadArgsModel:
        turn = 0

        def complete_with_tools(self, _messages, _tools):
            self.turn += 1
            if self.turn == 1:
                return ChatCompletion("", (ToolCall("call-1", "list_requirements", "not-json"),))
            return ChatCompletion("参数已修正后无法继续查询。", ())

    monkeypatch.setattr(
        "requirement_agent.agents.assistant_agent.llm_schemas",
        lambda _consumer: [{"name": "list_requirements"}],
    )
    result = RequirementAssistantAgent(model=BadArgsModel()).answer("有哪些需求？")

    assert result.status == "completed"
    assert result.tool_calls[0]["status"] == "error"
    assert "参数不合法" in str(result.tool_calls[0]["message"])


def test_auto_mode_prefers_read_only_library_query() -> None:
    service = IntentService()

    assert service.classify("登录模块有哪些需求？").intent == "library_query"
    assert service.classify("新增手机号登录并整理提案").intent == "new_requirement"
    assert service.classify("删除 REQ-1").intent == "operation_request"
    assert service.classify("查看新增需求有哪些").intent == "library_query"
    assert service.classify("删除功能有哪些需求").intent == "library_query"


def test_react_agent_checks_stop_and_uses_bounded_chat_history(monkeypatch) -> None:
    class HistoryModel:
        def __init__(self) -> None:
            self.messages = []

        def complete_with_tools(self, messages, _tools):
            self.messages = messages
            return ChatCompletion("无需调用工具。", ())

    model = HistoryModel()
    result = RequirementAssistantAgent(model=model).answer(
        "现在的问题", history=[{"role": "user", "content": f"历史{i}"} for i in range(10)]
    )
    assert result.status == "completed"
    assert [item["content"] for item in model.messages if item["role"] == "user"] == [*(f"历史{i}" for i in range(2, 10)), "现在的问题"]

    stopped = RequirementAssistantAgent(model=model).answer("问题", should_stop=lambda: "cancelled")
    assert stopped.status == "cancelled"


def test_paged_evidence_and_unknown_citation_are_checked(monkeypatch) -> None:
    class CitationModel:
        turn = 0

        def complete_with_tools(self, _messages, _tools):
            self.turn += 1
            if self.turn == 1:
                return ChatCompletion("", (ToolCall("call-1", "list_requirements_by_module", '{"module":"登录"}'),))
            return ChatCompletion("见 [E2]。", ())

    monkeypatch.setattr("requirement_agent.agents.assistant_agent.invoke", lambda *_args, **_kwargs: ToolResult.success({"items": [{"requirement_key": "REQ-1", "requirement_name": "登录"}]}))
    monkeypatch.setattr("requirement_agent.agents.assistant_agent.llm_schemas", lambda _consumer: [{"name": "list_requirements_by_module"}])
    result = RequirementAssistantAgent(model=CitationModel()).answer("登录模块")
    assert result.status == "incomplete"
    assert result.incomplete_reason == "invalid_citation:E2"
    assert result.sources()[0]["source_ref"] == "REQ-1"
