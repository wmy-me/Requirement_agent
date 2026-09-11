"""会话（conversations）HTTP 路由（子批次 3.3.4 迁移）。

来源：`src/interfaces/http/rest.py` 的 conversations 域名下 7 条路由，原样逐字迁移：
`GET/POST /api/v1/conversations`、`GET/POST /api/v1/conversations/{id}/messages`、
`PATCH/DELETE /api/v1/conversations/{id}`、`POST /api/v1/conversations/{id}/finalize`。

旧文件通过 `include_router` 在原位置复用本 router（同一实现，不重复注册）。

为何 7 条一起迁移：只读与写路由在原文件中**交错排列**（GET list → POST create → GET messages
→ POST add → PATCH → DELETE → POST finalize），无法单独抽出只读组而不打乱注册顺序；
整块连续，故作为一个 router 迁移以保持顺序。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from requirement_agent.api.dependencies import (
    actor_id_or_default,
    chat_repo,
    memory_extractor,
    memory_repo,
    summarize_text,
)
from requirement_agent.api.schemas import (
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
)

router = APIRouter()


@router.get("/api/v1/conversations")
async def list_conversations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """会话列表：按 actor 分页返回会话 {"items": [...]}。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    return {"items": chat_repo.list_conversations(actor_id=normalized_actor_id, limit=limit, offset=offset)}


@router.post("/api/v1/conversations")
async def create_conversation(payload: ConversationCreateRequest) -> dict[str, object]:
    """新建会话：按标题与 actor 创建，返回会话对象。"""
    actor_id = actor_id_or_default(payload.actor_id)
    conversation = chat_repo.create_conversation(actor_id=actor_id, title=payload.title)
    return conversation


@router.get("/api/v1/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """按会话 id 分页读取消息，返回 {"conversation_id", "items": [...]}。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    conversation = chat_repo.get_conversation(conversation_id, actor_id=normalized_actor_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"conversation_id": conversation_id, "items": chat_repo.get_messages(conversation_id)}


@router.post("/api/v1/conversations/{conversation_id}/messages")
async def add_conversation_message(conversation_id: str, payload: ConversationMessageCreateRequest) -> dict[str, object]:
    """向会话追加一条用户消息，返回落库后的消息对象 {"message": ...}。"""
    actor_id = actor_id_or_default(payload.actor_id)
    conversation = chat_repo.get_conversation(conversation_id, actor_id=actor_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    message = chat_repo.upsert_user_message(
        conversation_id=conversation_id,
        content=payload.message,
        actor_id=actor_id,
        client_message_id=payload.client_message_id or str(uuid4()),
    )
    return {"message": message}


@router.patch("/api/v1/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, payload: ConversationUpdateRequest, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """更新会话标题/摘要/状态；会话不存在返回 404，成功返回更新后的会话对象。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    conversation = chat_repo.update_conversation(
        conversation_id,
        title=payload.title,
        summary=payload.summary,
        status=payload.status,
        actor_id=normalized_actor_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


@router.delete("/api/v1/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, str]:
    """删除会话：成功返回 {"status": "deleted"}，不存在返回 404。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    deleted = chat_repo.delete_conversation(conversation_id, actor_id=normalized_actor_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/api/v1/conversations/{conversation_id}/finalize")
async def finalize_conversation(conversation_id: str, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """会话收尾：生成一句话摘要 + 触发长期记忆抽取。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    conversation = chat_repo.get_conversation(conversation_id, actor_id=normalized_actor_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    messages = chat_repo.get_messages(conversation_id)
    transcript = "\n".join(str(msg["content"] or "") for msg in messages if msg["role"] in {"user", "assistant"})
    summary = summarize_text(transcript)
    chat_repo.update_conversation(conversation_id, summary=summary, actor_id=normalized_actor_id)
    memory = memory_extractor.extract_from_conversation(
        actor_id=normalized_actor_id,
        conversation_id=conversation_id,
        messages=messages,
        existing_notes=memory_repo.list_memories(actor_id=normalized_actor_id, limit=50),
    )
    return {"status": "finalized", "conversation_id": conversation_id, "summary": summary, "memory": memory}
