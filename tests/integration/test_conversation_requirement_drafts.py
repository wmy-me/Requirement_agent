"""会话草稿与正式需求来源必须分层，普通聊天绝不能污染正式链路。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.api.dependencies import chat_repo
from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.session import SessionLocal


def _cleanup(conversation_id: str) -> None:
    with SessionLocal() as session:
        source_ids = session.execute(
            text("SELECT source_id FROM conversation_requirement_draft WHERE conversation_id = CAST(:id AS UUID)"),
            {"id": conversation_id},
        ).scalars().all()
        session.execute(text("DELETE FROM agent_run WHERE conversation_id = CAST(:id AS UUID)"), {"id": conversation_id})
        session.execute(text("DELETE FROM agent_message WHERE conversation_id = CAST(:id AS UUID)"), {"id": conversation_id})
        session.execute(text("DELETE FROM agent_conversation WHERE id = CAST(:id AS UUID)"), {"id": conversation_id})
        if source_ids:
            session.execute(text("DELETE FROM requirement_source WHERE id = ANY(:ids)"), {"ids": source_ids})
        session.commit()


def test_chat_messages_do_not_create_requirement_drafts_or_sources() -> None:
    conversation_id = str(chat_repo.create_conversation(actor_id="draft-test", title="普通聊天")['id'])
    try:
        with SessionLocal() as session:
            before = session.execute(text("SELECT count(*) FROM requirement_source")).scalar_one()
        chat_repo.upsert_user_message(
            conversation_id=conversation_id, actor_id="draft-test", client_message_id="who", content="你是谁？"
        )
        chat_repo.upsert_user_message(
            conversation_id=conversation_id, actor_id="draft-test", client_message_id="what", content="你能做什么？"
        )
        assert chat_repo.list_requirement_drafts(conversation_id) == []
        with SessionLocal() as session:
            assert session.execute(text("SELECT count(*) FROM requirement_source")).scalar_one() == before
    finally:
        _cleanup(conversation_id)


def _sse_events(text_body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in text_body.strip().split("\n\n"):
        name = "message"
        payload: dict[str, object] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip() or "{}")
        if block:
            events.append((name, payload))
    return events


def test_stream_casual_chat_does_not_start_requirement_pipeline() -> None:
    client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})
    conversation_id = ""
    try:
        with SessionLocal() as session:
            before_sources = session.execute(text("SELECT count(*) FROM requirement_source")).scalar_one()

        response = client.post(
            "/api/v1/agent/chat/stream",
            json={"message": "你好你是谁", "actor_id": "draft-test", "client_message_id": "casual-1"},
        )

        assert response.status_code == 200
        events = _sse_events(response.text)
        session_event = next(payload for name, payload in events if name == "session")
        conversation_id = str(session_event["session_id"])
        assert session_event["intent"] == "casual_chat"
        assert not any(name == "artifacts" for name, _ in events)
        assert "只有明确像需求材料时" in response.text

        conversation = chat_repo.get_conversation(conversation_id, actor_id="draft-test")
        assert conversation is not None
        assert conversation["title"] == "了解需求助手"
        assert chat_repo.list_requirement_drafts(conversation_id) == []

        with SessionLocal() as session:
            assert session.execute(text("SELECT count(*) FROM requirement_source")).scalar_one() == before_sources
            assert session.execute(
                text("SELECT count(*) FROM agent_run WHERE conversation_id = CAST(:id AS UUID)"),
                {"id": conversation_id},
            ).scalar_one() == 0
    finally:
        if conversation_id:
            _cleanup(conversation_id)


def test_three_drafts_revision_and_explicit_submit() -> None:
    client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})
    conversation_id = str(chat_repo.create_conversation(actor_id=settings.api_actor_id, title="草稿边界")['id'])
    try:
        created = []
        for content in ("支持导出审批记录", "支持按部门筛选", "支持提醒待审核需求"):
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/requirement-drafts", json={"content": content}
            )
            assert response.status_code == 201
            created.append(response.json()["draft"])
            assert isinstance(created[-1]["id"], str)

        listed = client.get(f"/api/v1/conversations/{conversation_id}/requirement-drafts")
        assert listed.status_code == 200
        assert [item["content"] for item in listed.json()["items"]] == [item["content"] for item in created]

        revised = client.post(
            f"/api/v1/conversations/{conversation_id}/requirement-drafts/{created[-1]['id']}/revise",
            json={"content": "支持每日提醒待审核需求"},
        )
        assert revised.status_code == 201
        revised_draft = revised.json()["draft"]
        assert revised_draft["parent_draft_id"] == created[-1]["id"]
        all_drafts = client.get(f"/api/v1/conversations/{conversation_id}/requirement-drafts").json()["items"]
        assert next(item for item in all_drafts if item["id"] == created[-1]["id"])["status"] == "superseded"

        # 仅在显式提交时创建来源；且异步入口返回 received，绝不直接写 requirement_master。
        submitted = client.post(
            f"/api/v1/conversations/{conversation_id}/requirement-drafts/{revised_draft['id']}/submit", json={}
        )
        assert submitted.status_code == 202
        payload = submitted.json()
        assert isinstance(payload["source_id"], str)
        assert payload["status"] == "received"
        assert payload["draft"]["status"] == "submitted"
        assert payload["draft"]["source_id"] == payload["source_id"]
    finally:
        _cleanup(conversation_id)
