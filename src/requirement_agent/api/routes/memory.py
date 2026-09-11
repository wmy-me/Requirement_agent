"""长期记忆（memory）HTTP 路由（子批次 3.3.5 迁移）。

来源：`src/interfaces/http/rest.py` 的 4 条记忆路由（list / upsert / delete / context），逐字迁移。
旧文件通过 `include_router` 在原位置复用本 router。本 router 不设 tags，由父 router 补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.requirement_agent.config.settings import settings
from src.requirement_agent.api.dependencies import (
    actor_id_or_default,
    embedding_service,
    memory_context_builder,
    memory_repo,
)

router = APIRouter()


@router.get("/api/v1/memory")
async def list_memory(actor_id: str | None = Query(default=None, max_length=120), limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    """长期记忆列表：按 actor 分页返回 {"items": [...]}。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    return {"items": memory_repo.list_memories(actor_id=normalized_actor_id, limit=limit)}


@router.post("/api/v1/memory")
async def upsert_memory(payload: dict[str, object]) -> dict[str, object]:
    """写入一条长期记忆：按 actor 落库并生成向量，返回记忆对象。"""
    actor_id = actor_id_or_default(str(payload.get("actor_id") or settings.api_actor_id or "api-user"))
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="memory content is required")
    embedding = None
    try:
        vector = embedding_service.embed(content)
        if isinstance(vector, list) and vector:
            embedding = [float(v) for v in vector]
    except Exception:
        embedding = None
    note = memory_repo.insert_memory(
        actor_id=actor_id,
        kind=str(payload.get("kind") or "fact"),
        content=content,
        source_conversation_id=payload.get("source_conversation_id"),
        source_message_id=int(payload["source_message_id"]) if payload.get("source_message_id") is not None else None,
        ref_requirement_key=payload.get("ref_requirement_key"),
        importance=int(payload.get("importance") or 1),
        meta=dict(payload.get("meta") or {}),
        embedding=embedding,
    )
    return note


@router.post("/api/v1/memory/{memory_id}/delete")
async def delete_memory(memory_id: int, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """软删除一条记忆：成功返回 {"status": "deleted"}，越权或不存在返回 404。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    note = memory_repo.update_status(memory_id, "deleted")
    if note is None or note["actor_id"] != normalized_actor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    return {"status": "deleted", "memory": note}


@router.get("/api/v1/memory/context")
async def get_memory_context(query: str = Query(min_length=1, max_length=200), actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """构建长期记忆上下文：按 query 检索相关记忆，返回 {"context": ...}。"""
    normalized_actor_id = actor_id_or_default(actor_id)
    return {"context": memory_context_builder.build_context(normalized_actor_id, query, limit=4)}
