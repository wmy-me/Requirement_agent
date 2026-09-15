"""续跑（C 批）：从断点继续分析，**不重算已完成阶段**。

用一个 stage=assessed（四步全已完成）的 run + 完整 checkpoint 续跑，
断言 extract/retrieve/analyze/risk 的 agent 与检索**完全不被调用**，
只有叙事（narrative）重新生成。不碰真实 LLM：agent 用哨兵替换、
LLMProvider 用假流、记忆召回置空。
"""

import asyncio
import uuid

import pytest

from requirement_agent.api.dependencies import chat_repo
from requirement_agent.api.routes import agent_chat
from requirement_agent.infrastructure.db.session import SessionLocal


class BoomAgent:
    """一旦被调用就抛错 —— 续跑若重算了这些阶段，测试立即失败。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    def extract(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"不应重算 extract（{self.label}）")

    def analyze(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"不应重算 analyze（{self.label}）")

    def assess(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"不应重算 risk（{self.label}）")


class BoomRetrieval:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, *a, **k):
        self.calls += 1
        raise AssertionError("不应重算 retrieve")


class FakeStreamProvider:
    def __init__(self) -> None:
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def generate_stream(self, prompt: str, system_prompt: str | None = None):
        self.calls += 1
        yield "（续跑生成的叙事）"


def _make_checked_run() -> dict[str, object]:
    conv = str(chat_repo.create_conversation(actor_id="resume-test", title="续跑测试")["id"])
    run = chat_repo.create_run(conversation_id=conv, client_message_id="m-1", status="cancelled")
    run_id = str(run["run_id"])
    checkpoint = {
        "extracted": {
            "requirement_title": "排期", "summary": "按优先级排期", "business_domain": "general",
            "priority": "medium", "tags": [], "requirements": [], "raw_text": "希望支持按优先级排期",
        },
        "candidates": [{"requirement_key": "REQ-000001", "similarity": 0.6}],
        "analysis": {"duplicate": False, "related": False, "conflict": False, "independent": True},
        "risk": {"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low", "confidence": 0.7},
    }
    chat_repo.update_run(run_id=run_id, status="cancelled", stage="assessed", checkpoint=checkpoint)
    return {"conv": conv, "run": chat_repo.get_run(run_id)}


async def _collect(frames) -> list[str]:
    return [str(f) async for f in frames]


def test_resume_skips_all_completed_stages(monkeypatch) -> None:
    holder = _make_checked_run()
    conv, run = holder["conv"], holder["run"]
    try:
        # 用哨兵替换四步，用假流替换叙事 LLM，记忆召回置空
        boom_extract = BoomAgent("extract")
        boom_analyze = BoomAgent("analyze")
        boom_risk = BoomAgent("risk")
        boom_retrieval = BoomRetrieval()
        fake_stream = FakeStreamProvider()
        monkeypatch.setattr(agent_chat, "extract_agent", boom_extract)
        monkeypatch.setattr(agent_chat, "analyze_agent", boom_analyze)
        monkeypatch.setattr(agent_chat, "risk_agent", boom_risk)
        monkeypatch.setattr(agent_chat, "retrieval_service", boom_retrieval)
        monkeypatch.setattr(agent_chat, "LLMProvider", lambda: fake_stream)
        monkeypatch.setattr(agent_chat.memory_context_builder, "build_context", lambda *a, **k: None)

        frames = asyncio.run(_collect(agent_chat._stream_resumed_run(run)))

        # 核心：四步一个都不许重算
        assert boom_extract.calls == 0
        assert boom_analyze.calls == 0
        assert boom_risk.calls == 0
        assert boom_retrieval.calls == 0
        # 叙事确实重新生成了
        assert fake_stream.calls == 1
        # 产出与首跑一致的事件
        joined = "\n".join(frames)
        assert "artifacts" in joined and "narrative" in joined and "done" in joined

        # 续跑收尾：run 置为 completed + done
        final = chat_repo.get_run(str(run["run_id"]))
        assert final["status"] == "completed" and final["stage"] == "done"
    finally:
        from sqlalchemy import text

        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM agent_run WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.execute(
                text("DELETE FROM agent_message WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.execute(
                text("DELETE FROM agent_conversation WHERE id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.commit()


def test_resume_endpoint_validates_bad_runs() -> None:
    """不存在的 run / 已完成的 run / 无断点的 run 都要被挡。"""
    from fastapi.testclient import TestClient

    from requirement_agent.api.app import app

    c = TestClient(app)
    # 不存在的 run
    assert c.post(f"/api/v1/agent/runs/{uuid.uuid4()}/resume").status_code == 404
    # 已完成的 run
    conv = str(chat_repo.create_conversation(actor_id="resume-test2", title="已完成")["id"])
    try:
        run = chat_repo.create_run(conversation_id=conv, client_message_id="m-2", status="completed")
        assert c.post(f"/api/v1/agent/runs/{run['run_id']}/resume").status_code == 409
        # 无断点的 failed run
        run2 = chat_repo.create_run(conversation_id=conv, client_message_id="m-3", status="failed")
        assert c.post(f"/api/v1/agent/runs/{run2['run_id']}/resume").status_code == 409
    finally:
        from sqlalchemy import text

        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM agent_run WHERE conversation_id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.execute(
                text("DELETE FROM agent_conversation WHERE id = CAST(:c AS UUID)"), {"c": conv}
            )
            session.commit()
