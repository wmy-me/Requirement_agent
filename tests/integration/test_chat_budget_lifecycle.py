"""聊天/ReAct 的预算传播与断点续跑边界（真库真行）。

盯住四件事：

1. **预算耗尽是 `incomplete`**：run 收尾为 `failed` + `meta.outcome=incomplete` +
   最终事件 `run_incomplete`，且消息仍落库可回放（与 provider 失败、用户取消区分）。
2. **暂停落在耗时节点期间**：动态图的旧执行者不得把 run 覆写成 completed，
   也不得覆盖暂停时写下的 stage/checkpoint。
3. **续跑不重置预算**：`agent_run.meta.deadline_at_epoch` 是绝对时刻，
   续跑沿用原值，而不是重新发一整份 `AGENT_ANALYSIS_BUDGET_SECONDS`。
4. **解不开的动态断点不得回退到无 token 的旧写入路径**：旧四段管线只服务
   真正的历史断点（没有 `version` 键），否则失败关闭。

不碰真实模型：分析图与叙事 LLM 都用假件替换。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import text

from requirement_agent.api.dependencies import chat_repo
from requirement_agent.agents.assistant_agent import AssistantResult
from requirement_agent.api.routes import agent_chat
from requirement_agent.application.chat_checkpoint import pack as pack_checkpoint
from requirement_agent.application.execution_control import remaining_seconds
from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.repositories import AgentRunRepository
from requirement_agent.infrastructure.db.session import SessionLocal


async def _frames(stream) -> list[str]:
    return [str(frame) async for frame in stream]


class _FakeStreamProvider:
    """叙事用的假流式 LLM：不联外网、结果稳定。"""

    def is_configured(self) -> bool:
        return True

    def generate_stream(self, prompt: str, system_prompt: str | None = None):
        yield "（假叙事）"


@pytest.fixture
def conversation():
    conv = str(chat_repo.create_conversation(actor_id="chat-budget", title="预算测试")["id"])
    yield conv
    with SessionLocal() as session:
        session.execute(text("DELETE FROM agent_run WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv})
        session.execute(text("DELETE FROM agent_message WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv})
        session.execute(text("DELETE FROM agent_conversation WHERE id = CAST(:c AS UUID)"), {"c": conv})
        session.commit()


# ── ① 预算耗尽 → incomplete（不是「模型不可用」，也不是成功）───────────────


def test_exhausted_chat_budget_finishes_incomplete_and_stays_replayable(monkeypatch, conversation) -> None:
    # 把助手预算压成 0：run 一建出来剩余预算就是 0，ReAct 循环第一个边界即停。
    monkeypatch.setattr(settings, "agent_assistant_budget_seconds", 0.0)

    frames = asyncio.run(_frames(agent_chat._stream_library_assistant(
        session_id=conversation, actor_id="chat-budget", client_message_id="budget-0",
        message="登录模块有哪些需求？", history=[], allow_external=False, intent="library_query",
    )))

    run = chat_repo.get_run_by_client(conversation, "budget-0")
    run_id = str(run["run_id"])
    assert run["status"] == "failed", "预算耗尽不是成功"
    assert run["meta"]["outcome"] == "incomplete", "必须与 provider 失败/取消区分"
    assert run["meta"]["deadline_at_epoch"] is not None, "创建时就要落绝对 deadline"

    events = AgentRunRepository().list_events(run_id)
    assert events[-1]["event_type"] == "run_incomplete"
    assert "run_completed" not in [event["event_type"] for event in events]

    # 消息仍要落库：断线重连要能回放这次「不完整」的答复。
    assistant = chat_repo.get_assistant_message_for_run(run_id)
    assert assistant is not None
    assert "不完整" in assistant["content"]
    assert "incomplete" in "\n".join(frames)


# ── ② 暂停落在节点执行期间 ────────────────────────────────────────────────


def test_assistant_react_loop_runs_under_the_run_deadline(monkeypatch, conversation) -> None:
    """ReAct 循环必须跑在 run 的绝对 deadline 之下。

    没有这层绑定，provider 拿不到剩余预算（`remaining_seconds()` 是 None），
    每次 HTTP 请求都会退回全局默认 timeout —— 预算就只在循环边界被检查，
    单次请求仍可能远超剩余时间。工具调用同理（`invoke(..., remaining_seconds=)`）。
    """
    seen: list[float | None] = []

    class _RecordingAgent:
        def __init__(self, deadline_seconds: float) -> None:
            self.deadline_seconds = deadline_seconds

        def answer(self, question: str, **kwargs) -> AssistantResult:
            seen.append(remaining_seconds())
            return AssistantResult("（假回答）", "completed", [], [], [])

    monkeypatch.setattr(
        agent_chat, "RequirementAssistantAgent", lambda deadline_seconds: _RecordingAgent(deadline_seconds)
    )

    frames = asyncio.run(_frames(agent_chat._stream_library_assistant(
        session_id=conversation, actor_id="chat-budget", client_message_id="deadline-bound",
        message="有哪些需求？", history=[], allow_external=False, intent="library_query",
    )))

    assert seen and seen[0] is not None, "ReAct 循环没有绑定 run deadline，模型调用拿不到剩余预算"
    assert 0 < seen[0] <= settings.agent_assistant_budget_seconds
    assert '"status": "completed"' in "\n".join(frames)


def test_pause_landing_inside_a_node_keeps_the_run_paused(monkeypatch, conversation) -> None:
    """用户在耗时节点期间点暂停：旧执行者不得覆写终态或 checkpoint。"""
    pause_marker = {"stage": "routing", "checkpoint": {"version": 1, "state": {"source_text": "新增导出"}}}

    def pausing_graph(_initial):
        run = chat_repo.get_active_run(conversation)
        assert run is not None
        # 用真实仓储模拟 pause 端点：状态改 paused，并写下暂停时的 stage/checkpoint。
        chat_repo.update_run(
            run_id=str(run["run_id"]), status="paused", error=None, meta=run["meta"],
            stage=pause_marker["stage"], checkpoint=pause_marker["checkpoint"],
        )
        yield {"router": {"intent": "requirement", "route_action": "extract", "route_reason": "先抽取",
                          "route_count": 1, "route_history": [{"action": "extract"}], "run_events": []}}

    monkeypatch.setattr(agent_chat, "stream_analysis", pausing_graph)

    frames = asyncio.run(_frames(agent_chat._stream_chat_pipeline(
        session_id=conversation, actor_id="chat-budget", client_message_id="paused-node",
        user_content="新增导出", user_meta=None, run_text="新增导出", source_type="web",
        requester_name=None, analysis_mode="strict", history=[],
    )))

    run = chat_repo.get_run_by_client(conversation, "paused-node")
    run_id = str(run["run_id"])
    assert run["status"] == "paused", "暂停必须赢，不能被旧执行者覆写成终态"
    assert run["stage"] == "routing" and run["checkpoint"] == pause_marker["checkpoint"]
    kinds = [event["event_type"] for event in AgentRunRepository().list_events(run_id)]
    assert "run_completed" not in kinds and "run_incomplete" not in kinds
    assert '"status": "paused"' in "\n".join(frames)


# ── ③ 续跑沿用原预算，不重新发一份 ────────────────────────────────────────


def test_resume_reuses_the_persisted_deadline_instead_of_minting_a_new_budget(
    monkeypatch, conversation
) -> None:
    run = chat_repo.create_run(conversation_id=conversation, client_message_id="resume-keep", status="paused")
    run_id = str(run["run_id"])
    original_deadline = time.time() + 90  # 「还剩下的」预算，续跑不许把它推后
    chat_repo.update_run(
        run_id=run_id, status="paused", error=None,
        meta={"source_type": "web", "checkpoint_format": "dynamic-v1", "budget_seconds": 240.0,
              "deadline_at_epoch": original_deadline},
        stage="extracted",
        checkpoint=pack_checkpoint({"source_text": "希望支持按优先级排期", "completed_steps": ["extract"]}),
    )

    def continuing_graph(initial):
        assert initial["source_text"] == "希望支持按优先级排期"
        yield {"router": {"intent": "requirement", "route_action": "answer", "route_reason": "直接回答",
                          "route_count": 2, "route_history": [{"action": "answer"}], "run_events": []}}
        yield {"answer": {"answer": "已从断点继续", "completed_steps": ["answer"], "run_events": []}}

    monkeypatch.setattr(agent_chat, "stream_analysis", continuing_graph)
    monkeypatch.setattr(agent_chat, "LLMProvider", lambda: _FakeStreamProvider())
    monkeypatch.setattr(agent_chat.memory_context_builder, "build_context", lambda *a, **k: None)

    frames = asyncio.run(_frames(agent_chat._stream_resumed_run(chat_repo.get_run(run_id))))

    final = AgentRunRepository().get_run(run_id)
    assert final["status"] == "completed", "\n".join(frames)
    assert final["meta"]["deadline_at_epoch"] == original_deadline, (
        "续跑重新发了一整份预算 —— 暂停/续跑可以无限刷新 deadline，"
        "`AGENT_ANALYSIS_BUDGET_SECONDS` 就不再是上界"
    )
    assert "\"status\": \"completed\"" in "\n".join(frames)


# ── ④ 解不开的动态断点不得退回无 token 的旧写入路径 ────────────────────────


def test_unreadable_dynamic_checkpoint_is_refused_not_written_by_the_legacy_pipeline(
    monkeypatch, conversation
) -> None:
    run = chat_repo.create_run(conversation_id=conversation, client_message_id="corrupt-cp", status="paused")
    run_id = str(run["run_id"])
    broken = pack_checkpoint({"source_text": "新增导出", "completed_steps": ["extract"]})
    broken["checksum"] = "0" * 64  # 带 version 的动态断点，但校验失败
    chat_repo.update_run(
        run_id=run_id, status="paused", error=None,
        meta={"source_type": "web", "checkpoint_format": "dynamic-v1", "deadline_at_epoch": time.time() + 60},
        stage="extracted", checkpoint=broken,
    )

    async def forbidden_legacy(**_kwargs):
        raise AssertionError("新执行的断点不得走无 token 的四段兼容写入路径")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_chat, "_stream_chat_pipeline_legacy", forbidden_legacy)

    frames = asyncio.run(_frames(agent_chat._stream_resumed_run(chat_repo.get_run(run_id))))

    assert "error" in "\n".join(frames)
    # 旧管线会把 run 置成 running 再写终态；这里必须原样保留。
    after = chat_repo.get_run(run_id)
    assert after["status"] == "paused"
    assert after["stage"] == "extracted"


def test_genuine_legacy_checkpoint_still_routes_to_the_compat_pipeline(monkeypatch, conversation) -> None:
    """反向保证：真正的历史断点（没有 version 键）仍必须能续跑。"""
    run = chat_repo.create_run(conversation_id=conversation, client_message_id="legacy-cp", status="paused")
    run_id = str(run["run_id"])
    chat_repo.update_run(
        run_id=run_id, status="paused", error=None, meta={"source_type": "web"},
        stage="extracted", checkpoint={"extracted": {"raw_text": "历史断点"}},
    )

    seen: list[str] = []

    async def legacy_stub(**kwargs):
        seen.append(str(kwargs["resume"]["stage"]))
        yield agent_chat._sse("done", {"run_id": kwargs.get("client_message_id")})

    monkeypatch.setattr(agent_chat, "_stream_chat_pipeline_legacy", legacy_stub)

    frames = asyncio.run(_frames(agent_chat._stream_resumed_run(chat_repo.get_run(run_id))))

    assert seen == ["extracted"], "历史断点必须继续走兼容路径"
    assert "done" in "\n".join(frames)


# ── ⑤ 澄清：终态走 token 约束的原子收尾，checkpoint 是版本化的 ──────────────


def test_clarification_is_persisted_with_a_claimed_token(monkeypatch, conversation) -> None:
    def clarifying_graph(_initial):
        yield {"router": {"intent": "requirement", "route_action": "extract", "route_reason": "先抽取",
                          "route_count": 1, "route_history": [{"action": "extract"}], "run_events": []}}
        yield {"router": {"intent": "requirement", "route_action": "clarify", "route_reason": "缺验收",
                          "route_count": 2, "route_history": [{"action": "clarify"}],
                          "missing_information": ["请补充验收标准"], "run_events": []}}
        yield {"clarify": {"clarification_question": "请补充验收标准",
                           "completed_steps": ["clarify"], "run_events": []}}

    monkeypatch.setattr(agent_chat, "stream_analysis", clarifying_graph)

    frames = asyncio.run(_frames(agent_chat._stream_chat_pipeline(
        session_id=conversation, actor_id="chat-budget", client_message_id="clarify-1",
        user_content="新增导出", user_meta=None, run_text="新增导出", source_type="web",
        requester_name=None, analysis_mode="strict", history=[],
    )))

    pending = chat_repo.find_clarification_run(conversation)
    assert pending is not None and pending["status"] == "waiting_clarification"
    run = AgentRunRepository().get_run(str(pending["run_id"]))
    assert run["execution_token"], "澄清结果必须由持有执行权的执行者写入"
    assert run["checkpoint"]["version"] == 1, "动态断点必须版本化，才不会被误当作历史断点"
    assert "请补充验收标准" in pending["checkpoint"]["state"]["clarification_question"]
    assert agent_chat.unpack_checkpoint(dict(run["checkpoint"]))[0] is not None
    kinds = [event["event_type"] for event in AgentRunRepository().list_events(str(pending["run_id"]))]
    assert kinds[-1] == "run_completed"
    assert "waiting_clarification" in "\n".join(frames)
