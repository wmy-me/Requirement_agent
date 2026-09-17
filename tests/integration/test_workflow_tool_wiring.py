"""固定工作流接工具注册表（B3）。

三件要钉住的：

1. **接线没有改变行为** —— `retrieve_node` 现在经注册表调 `search_requirements`，
   而它底层还是同一个 `RetrievalService.search`，所以候选应当**逐条相同**。
2. **检索失败不静默降级** —— 这是本批次最重要的一条设计判断。检索是判重复的唯一
   依据，空候选会让 analyze 得出「独立」，而「错误合并污染版本链」正是要防的头号问题。
   所以失败必须**上抛**，走既有的可见路径（outbox 重试 → 死信 → 运维页）。
3. **调用留痕** —— 每次调用在 state 里留下 name / params / status / 耗时 / 条数。

打真实库（happy path 要真实检索）。
"""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest

from requirement_agent.agents.retrieval_agent import RetrievalAgent
from requirement_agent.tools.base import ToolResult, ToolStatus
from requirement_agent.tools.invoker import (
    ToolInvocationError,
    invoke,
    is_ok,
    tool_call_record,
)
from requirement_agent.workflows import agents_nodes
from requirement_agent.workflows.agents_nodes import retrieve_node

_QUERY = "门店巡检计划"


def _state(query: str = _QUERY) -> dict:
    return {"extracted": {"summary": query}, "source_text": query, "source_type": "web"}


# ── ① 接线行为等价 ────────────────────────────────────────────────────────


def test_retrieve_node_still_returns_candidates() -> None:
    result = retrieve_node(_state())
    assert result["candidates"], "真实库里应有与「门店巡检计划」相关的需求"
    # 与直接调检索 Agent 的结果**逐条相同** —— 接线只是换了条路，没换内核
    expected = RetrievalAgent().retrieve(_QUERY, limit=5)
    assert [c.get("requirement_key") for c in result["candidates"]] == [
        c.get("requirement_key") for c in expected
    ]


def test_retrieve_node_records_the_tool_call() -> None:
    """**「保存工具调用」的落点。** 留痕随 state 走，最终写进
    `requirement_source.metadata.tool_calls`（分析与落库分处两次调用，不存下来就查不到）。"""
    result = retrieve_node(_state())
    calls = result["tool_calls"]
    assert len(calls) == 1

    call = calls[0]
    assert call["tool"] == "search_requirements"
    assert call["status"] == "success"
    assert call["params"]["query"]
    assert call["duration_ms"] >= 0
    assert call["count"] == len(result["candidates"])


def test_empty_candidates_is_not_an_error() -> None:
    """查不到相似需求 → **正常继续**（不是失败）。

    与「检索挂了」是两回事：这一条是合法答案，那一条必须炸。
    三态结果的意义就在这里。
    """
    result = retrieve_node(_state("zzz这个词库里肯定没有zzz"))
    assert "candidates" in result
    assert result["tool_calls"][0]["status"] in {"success", "empty"}


# ── ② 失败不静默降级 ──────────────────────────────────────────────────────


def test_retrieve_failure_raises_instead_of_returning_empty(monkeypatch) -> None:
    """**本批次最重要的一条。**

    检索挂了却返回空候选，会让 `analyze` 得出「独立」—— 一个因基础设施故障而
    漏掉重复的需求被当成新需求入库，污染整条版本链。所以这里必须**炸**，
    让失败走 outbox 重试 → 死信 → 运维页那条可见路径。
    """
    monkeypatch.setattr(
        agents_nodes, "invoke",
        lambda *a, **k: ToolResult.error("数据库连接被拒绝"),
    )
    with pytest.raises(ToolInvocationError, match="search_requirements"):
        retrieve_node(_state())


def test_retrieve_failure_message_is_carried(monkeypatch) -> None:
    """异常里要带上**底层原因** —— 只写「工具失败」等于没写。"""
    monkeypatch.setattr(
        agents_nodes, "invoke",
        lambda *a, **k: ToolResult.error("connection refused by server"),
    )
    with pytest.raises(ToolInvocationError, match="connection refused"):
        retrieve_node(_state())


# ── ③ 统一调用入口 ────────────────────────────────────────────────────────


def test_invoke_unknown_tool_returns_error_not_raises() -> None:
    """名字写错是**编程错误** —— 让它出现在结果里比变成 AttributeError 好定位。"""
    result = invoke("no_such_tool", {})
    assert result.status is ToolStatus.ERROR
    assert "no_such_tool" in (result.message or "")


def test_invoke_enforces_consumer_whitelist() -> None:
    """`allowed_consumers` 是**声明式的权限边界**，在这里强制执行 ——
    而不只是写在描述里给人看。"""
    result = invoke("search_requirements", {"query": "巡检"}, consumer="analysis")
    assert is_ok(result)
    # 造一个不在白名单里的消费方
    denied = invoke("search_requirements", {"query": "巡检"}, consumer="not_a_consumer")
    assert denied.status is ToolStatus.ERROR
    assert "not_a_consumer" in (denied.message or "")


def test_is_ok_treats_empty_as_usable() -> None:
    """`EMPTY` 是**可用答案**，只有 `ERROR` 才算失败。"""
    assert is_ok(ToolResult.success([])) is True
    assert is_ok(ToolResult.empty("没有")) is True
    assert is_ok(ToolResult.error("炸了")) is False


# ── ④ 留痕的形状 ──────────────────────────────────────────────────────────


def test_tool_call_record_shape() -> None:
    record = tool_call_record(
        "search_requirements", {"query": "x", "limit": 5}, ToolResult.success([1, 2, 3])
    )
    assert set(record) == {"tool", "params", "status", "duration_ms", "count", "message"}
    assert record["count"] == 3
    assert record["params"] == {"query": "x", "limit": 5}


def test_tool_call_record_counts_dict_results() -> None:
    record = tool_call_record("get_requirement_detail", {}, ToolResult.success({"a": 1, "b": 2}))
    assert record["count"] == 2


def test_tool_call_record_keeps_error_message() -> None:
    record = tool_call_record("x", {}, ToolResult.error("炸了"))
    assert record["status"] == "error"
    assert record["message"] == "炸了"
