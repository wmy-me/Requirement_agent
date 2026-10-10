import asyncio

from sqlalchemy import text

from requirement_agent.api.dependencies import chat_repo
from requirement_agent.api.routes import agent_chat
from requirement_agent.application.chat_checkpoint import invalidate_for_supplement, unpack
from requirement_agent.infrastructure.db.session import SessionLocal


async def _frames(stream):
    return [frame async for frame in stream]


def _clarify_graph(_initial):
    yield {"router": {"intent": "requirement", "route_action": "extract", "route_reason": "先抽取", "route_count": 1, "route_history": [{"action": "extract"}], "run_events": []}}
    yield {"extract": {"extracted": {"raw_text": "新增导出", "requirements": ["导出"]}, "completed_steps": ["extract"], "run_events": []}}
    yield {"router": {"intent": "requirement", "route_action": "clarify", "route_reason": "缺验收", "route_count": 2, "route_history": [{"action": "clarify"}], "missing_information": ["请补充验收标准"], "run_events": []}}
    yield {"clarify": {"clarification_question": "为了继续分析，请补充验收标准", "completed_steps": ["clarify"], "run_events": []}}


def test_dynamic_checkpoint_is_versioned_and_continues_after_clarify(monkeypatch) -> None:
    conv = str(chat_repo.create_conversation(actor_id="dynamic-checkpoint", title="动态图") ["id"])
    try:
        monkeypatch.setattr(agent_chat, "stream_analysis", _clarify_graph)
        first = asyncio.run(_frames(agent_chat._stream_chat_pipeline(
            session_id=conv, actor_id="dynamic-checkpoint", client_message_id="first", user_content="新增导出",
            user_meta=None, run_text="新增导出", source_type="web", requester_name=None,
            analysis_mode="strict", history=[])))
        pending = chat_repo.find_clarification_run(conv)
        assert pending and pending["status"] == "waiting_clarification"
        state, error = unpack(pending["checkpoint"])
        assert error is None and state and "extract" in state["completed_steps"]
        assert '"step": "clarify"' in "".join(first)

        seen: dict[str, object] = {}
        def continued(initial):
            seen.update(initial)
            yield {"router": {"intent": "requirement", "route_action": "answer", "route_reason": "已补齐", "route_count": 3, "route_history": [{"action": "answer"}], "run_events": []}}
            yield {"answer": {"answer": "已收到验收标准", "completed_steps": ["answer"], "run_events": []}}
        monkeypatch.setattr(agent_chat, "stream_analysis", continued)
        second = asyncio.run(_frames(agent_chat._stream_chat_pipeline(
            session_id=conv, actor_id="dynamic-checkpoint", client_message_id="second", user_content="验收为 XLSX",
            user_meta=None, run_text="新增导出\n【补充信息】\n验收为 XLSX", source_type="web", requester_name=None,
            analysis_mode="strict", history=[], resume={
                "state": invalidate_for_supplement(state, "新增导出\n【补充信息】\n验收为 XLSX"),
                "parent_run_id": str(pending["run_id"]),
            })))
        # 补充改变 source_text；抽取及其下游证据必须失效，不能拿旧产物当新结论。
        assert seen["completed_steps"] == []
        assert seen["invalidation_history"][-1]["reason"] == "supplement_changed_source_text"
        assert chat_repo.get_run(str(pending["run_id"]))["status"] == "cancelled"
        assert "done" in "".join(second)
        # Atomic assistant finalization may add conversation/message fields but
        # must not erase the immutable lifecycle deadline or routing metadata.
        finished = chat_repo.get_run_by_client(conv, "second")
        assert finished and finished["meta"]["checkpoint_format"] == "dynamic-v1"
        assert "deadline_at_epoch" in finished["meta"]
    finally:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM agent_run WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv})
            session.execute(text("DELETE FROM agent_message WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv})
            session.execute(text("DELETE FROM agent_conversation WHERE id = CAST(:c AS UUID)"), {"c": conv})
            session.commit()
