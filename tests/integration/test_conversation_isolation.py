"""单对话并发隔离（docs/方案_对话状态机与Git式版本管理.md §2）。

语义两条：**同一对话一次只答一个问题；跨对话并行不受限**。
约束落在 `migrations/012` 的部分唯一索引上（键是 conversation_id），
而不是进程内的锁 —— 多 worker 下后者形同虚设。

打真实库，自带清理。这里不经过 HTTP 的流式端点跑管线（那会真调 LLM），
而是直接测仓库层与前置换算函数 `_ensure_conversation_idle`。
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from requirement_agent.api.dependencies import chat_repo
from requirement_agent.api.routes.agent_chat import _ensure_conversation_idle
from requirement_agent.common.snowflake import new_id
from requirement_agent.infrastructure.db.repositories.chat import ConversationBusyError
from requirement_agent.infrastructure.db.session import SessionLocal


def _new_conversation() -> str:
    return str(chat_repo.create_conversation(actor_id="isolation-test", title="隔离测试")["id"])


def _seed_run(conversation_id: str, *, age_days: float = 0, status: str = "running") -> None:
    """直接 INSERT 造 run。

    必须走 INSERT：`agent_run` 上有 `BEFORE UPDATE` 的 updated_at 触发器会把
    UPDATE 刷成 NOW()，用 UPDATE 伪造不出「很久没动」的状态。
    """
    with SessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO agent_run (id, run_id, conversation_id, client_message_id, status, updated_at)
                VALUES (:id, :run_id, CAST(:cid AS UUID), :cmid, :st,
                        NOW() - make_interval(secs => :age_secs))
                """
            ),
            {
                "id": new_id(),
                "run_id": str(uuid.uuid4()),
                "cid": conversation_id,
                "cmid": f"seed-{age_days}-{status}",
                "st": status,
                "age_secs": age_days * 86400,
            },
        )
        session.commit()


@pytest.fixture()
def conversations():
    """两个一次性会话，用完连同 run 一起删掉。"""
    a, b = _new_conversation(), _new_conversation()
    try:
        yield a, b
    finally:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM agent_run WHERE conversation_id = ANY(CAST(:ids AS UUID[]))"),
                {"ids": [a, b]},
            )
            session.execute(
                text("DELETE FROM agent_conversation WHERE id = ANY(CAST(:ids AS UUID[]))"),
                {"ids": [a, b]},
            )
            session.commit()


# ── 仓库层：同对话互斥、跨对话并行 ─────────────────────────────────────


def test_second_active_run_in_same_conversation_is_rejected(conversations) -> None:
    a, _b = conversations
    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="running")

    with pytest.raises(ConversationBusyError):
        chat_repo.create_run(conversation_id=a, client_message_id="m-2", status="running")


def test_different_conversations_run_in_parallel(conversations) -> None:
    """跨对话不阻塞 —— 这正是「开新对话问另一个」能成立的原因。"""
    a, b = conversations

    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="running")
    chat_repo.create_run(conversation_id=b, client_message_id="m-2", status="running")

    assert chat_repo.get_active_run(a) is not None
    assert chat_repo.get_active_run(b) is not None


def test_finished_run_does_not_block(conversations) -> None:
    """运行结束（completed）后同一个对话可以继续问。"""
    a, _b = conversations
    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="running")
    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="completed")

    assert chat_repo.get_active_run(a) is None
    # 能再开一条新的活跃运行
    chat_repo.create_run(conversation_id=a, client_message_id="m-2", status="running")


# ── 僵尸自愈：崩溃留下的 run 不能把对话永久堵死 ────────────────────────


def test_stale_run_is_expired_so_conversation_recovers(conversations) -> None:
    """静默超过阈值的活跃 run 会被判 failed —— 否则一次进程中断就永久锁死该对话。"""
    a, _b = conversations
    _seed_run(a, age_days=2)

    assert chat_repo.expire_stale_runs(a) == 1
    assert chat_repo.get_active_run(a) is None
    # 清掉之后可以正常再开
    chat_repo.create_run(conversation_id=a, client_message_id="m-after", status="running")


def test_fresh_run_is_not_expired(conversations) -> None:
    """刚建/还在跑的 run 不能被误判为僵尸（阈值 30 分钟，远大于正常分析耗时）。"""
    a, _b = conversations
    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="running")

    assert chat_repo.expire_stale_runs(a) == 0
    assert chat_repo.get_active_run(a) is not None


# ── 路由前置检查：友好 409（真正的互斥仍由索引兜底）────────────────────


def test_precheck_raises_409_with_active_run_info(conversations) -> None:
    a, _b = conversations
    chat_repo.create_run(conversation_id=a, client_message_id="m-1", status="running")

    with pytest.raises(HTTPException) as err:
        _ensure_conversation_idle(a)

    assert err.value.status_code == 409
    detail = err.value.detail
    assert "active_run" in detail and detail["active_run"]["status"] == "running"
    assert "新建对话" in detail["message"]  # 提示里要给出路，不能只是一个错


def test_precheck_passes_for_idle_and_unknown_conversations(conversations) -> None:
    a, b = conversations
    _ensure_conversation_idle(a)  # 空会话不拦
    _ensure_conversation_idle(None)  # 新对话（还没 session_id）不拦
    _ensure_conversation_idle(str(uuid.uuid4()))  # 不存在的会话不拦


def test_precheck_expires_stale_run_before_rejecting(conversations) -> None:
    """前置检查必须先让僵尸出局——否则 create_run 里的自愈永远走不到。"""
    a, _b = conversations
    _seed_run(a, age_days=2)

    _ensure_conversation_idle(a)  # 不应抛 409

    assert chat_repo.get_active_run(a) is None
