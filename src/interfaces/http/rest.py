"""REST 域：除 Agent 聊天外的所有 HTTP 端点。

归属路由前缀：requirements、documents、conversations、memory、reviews、sources、audit、health。
共享单例统一取自 `_state.py`；对话域在 `agent_chat.py`。
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from src.config.settings import settings
from src.domain.requirement import RequirementSource
from src.infrastructure.db.session import check_database_connection
from src.infrastructure.llm.openai_provider import LLMProvider
from src.interfaces.http._state import (
    actor_id_or_default,
    audit_repo,
    chat_repo,
    document_chunk_task,
    document_parser,
    document_repo,
    embedding_service,
    feature_repo,
    memory_context_builder,
    memory_extractor,
    memory_repo,
    object_storage,
    requirement_service,
    retrieval_service,
    review_service,
    source_repo,
    summarize_text,
    version_repo,
)
from src.interfaces.http.schemas import (
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
    RequirementSubmitRequest,
    RequirementSubmitResponse,
    ReviewSubmitRequest,
)

router = APIRouter(tags=["requirements"])


@router.get("/")
async def root() -> dict[str, str]:
    """服务入口：返回 API 名称与运行状态。"""
    return {"message": "Requirement Agent API", "status": "ok"}


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    """基础存活探活：固定返回 {"status": "ok"}。"""
    return {"status": "ok"}


# —— 健康检查组：已迁移至 requirement_agent.api.routes.health（子批次 3.2.3）——
# 在原位置 include 子 router；旧路径函数名继续可用（兼容转发）。
from src.requirement_agent.api.routes.health import (  # noqa: E402,F401
    database_health,
    llm_health,
)
from src.requirement_agent.api.routes.health import router as _health_router  # noqa: E402

router.include_router(_health_router)


@router.get("/api/v1/requirements")
async def list_requirements() -> dict[str, list[dict[str, object]]]:
    """需求列表：返回存量需求数组 {"items": [...]}。"""
    return {"items": requirement_service.list_requirements()}


@router.post("/api/v1/requirements/submit", response_model=RequirementSubmitResponse)
async def submit_requirement(payload: RequirementSubmitRequest) -> RequirementSubmitResponse:
    """提交文本需求进入评审流程：返回提交状态与 source_id。"""
    source = RequirementSource(
        idempotency_key=(
            payload.source_type
            + ":"
            + (payload.requester_id or "anonymous")
            + ":"
            + payload.original_text
        ),
        source_type=payload.source_type,
        source_event_id=payload.source_event_id,
        requester_id=payload.requester_id,
        requester_name=payload.requester_name,
        original_text=payload.original_text,
        original_payload={"input_mode": "text"},
        metadata=payload.metadata,
    )
    response = requirement_service.submit_requirement(source)
    return RequirementSubmitResponse(
        message="Requirement submitted for review",
        source_type=payload.source_type,
        status=str(response["status"]),
        source_id=response.get("source_id"),
    )


@router.post("/api/v1/requirements/ingest", response_model=RequirementSubmitResponse)
async def ingest_requirement(
    source_type: str = Form(default="web"),
    requester_id: str | None = Form(default=None),
    requester_name: str | None = Form(default=None),
    source_event_id: str | None = Form(default=None),
    original_text: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> RequirementSubmitResponse:
    """multipart 摄取需求：支持纯文本、文件或二者混合，返回提交状态与 source_id。"""
    normalized_text = (original_text or "").strip()
    parsed = None
    stored = None
    input_mode = "text"

    if file is not None:
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
        parsed = document_parser.parse(file.filename or "requirement-document.txt", payload)
        stored = object_storage.upload(file.filename or "requirement-document.txt", payload)
        input_mode = "mixed" if normalized_text else "document"
    elif not normalized_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="either original_text or file is required")

    merged_text = normalized_text
    if parsed is not None:
        merged_text = f"{normalized_text}\n\n{parsed.raw_content}".strip() if normalized_text else parsed.raw_content

    metadata_payload: dict[str, object] = {}
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be valid JSON") from exc
        if not isinstance(parsed_metadata, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be a JSON object")
        metadata_payload = parsed_metadata

    original_payload: dict[str, object] = {"input_mode": input_mode}
    if file is not None and parsed is not None and stored is not None:
        original_payload["file"] = {
            "name": file.filename,
            "content_type": file.content_type,
            "object_uri": stored.uri,
            "size": stored.size,
            "checksum": stored.checksum,
            "extension": parsed.extension,
            "pages": parsed.pages,
            "normalized_fields": parsed.normalized_fields or {},
            "segments": [
                {
                    "index": segment.index,
                    "kind": segment.kind,
                    "text": segment.text,
                    "field_name": segment.field_name,
                }
                for segment in parsed.segments or []
            ],
        }

    source = RequirementSource(
        idempotency_key=source_type + ":" + (requester_id or "anonymous") + ":" + merged_text,
        source_type=source_type,
        source_event_id=(source_event_id or "").strip() or None,
        requester_id=(requester_id or "").strip() or None,
        requester_name=(requester_name or "").strip() or None,
        original_text=merged_text,
        original_payload=original_payload,
        metadata=metadata_payload,
    )
    response = requirement_service.submit_requirement(source)

    # —— 文档入库后触发分片（解析出正文即入队，同步消费一次）——
    if file is not None and stored is not None:
        doc_asset = document_repo.save(
            file_name=file.filename or "requirement-document.txt",
            content_type=file.content_type or "application/octet-stream",
            storage_uri=stored.uri,
            checksum=stored.checksum,
            size_bytes=stored.size,
            source_type=source_type,
            original_text=merged_text,
            extracted_text=parsed.content if parsed is not None else merged_text,
            metadata={
                "input_mode": input_mode,
                "normalized_fields": parsed.normalized_fields or {} if parsed is not None else {},
                "segments": [
                    {"index": seg.index, "kind": seg.kind, "text": seg.text, "field_name": seg.field_name}
                    for seg in (parsed.segments or [])
                ] if parsed is not None else [],
                "source_id": response.get("source_id"),
            },
            source_id=response.get("source_id"),
        )
        if parsed is not None and parsed.content:
            document_chunk_task.enqueue(
                document_id=int(doc_asset["id"]),
                content=parsed.content,
                chunk_size=600,
                overlap=120,
            )
            document_chunk_task.process_pending(limit=1)

    return RequirementSubmitResponse(
        message="Requirement submitted for review",
        source_type=source_type,
        status=str(response["status"]),
        source_id=response.get("source_id"),
    )


# —— 相似需求检索组：已迁移至 requirement_agent.api.routes.requirements（子批次 3.2.2）——
# 在原位置 include 独立子 router；旧路径函数名继续可用（兼容转发）。
from src.requirement_agent.api.routes.requirements import (  # noqa: E402,F401
    search_requirement_features,
    search_requirements,
)
from src.requirement_agent.api.routes.requirements import search_router as _requirements_search_router  # noqa: E402

router.include_router(_requirements_search_router)


@router.get("/api/v1/documents")
async def list_documents(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    """文档资产列表：返回文档数组 {"items": [...]}。"""
    return {"items": document_repo.list_documents(limit=limit)}


@router.get("/api/v1/documents/search")
async def search_document_chunks(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict[str, object]:
    """按向量相似度检索文档分块：返回 {"items": [...]}。

    必须声明在 `/documents/{document_id}` 之前——否则 "search" 会被当作
    document_id 走 422（FastAPI 按声明顺序匹配路径参数路由）。
    """
    return {"items": document_repo.search_chunks(q.strip(), limit=limit)}


@router.get("/api/v1/documents/{document_id}")
async def get_document(document_id: int) -> dict[str, object]:
    """文档详情：按 id 返回单篇文档；不存在返回 404。"""
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.get("/api/v1/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: int,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, object]:
    """文档分块列表：按 id 返回分块数组 {"document_id", "items": [...]}。"""
    return {"document_id": document_id, "items": document_repo.get_chunks(document_id, limit=limit)}


@router.post("/api/v1/documents/{document_id}/reindex")
async def reindex_document_chunks(document_id: int) -> dict[str, object]:
    """重新切分并索引文档正文：返回 {"status": "queued", "document_id", ...}。"""
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    text = (document.get("extracted_text") or document.get("original_text") or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document text is empty")
    result = document_chunk_task.enqueue(document_id=document_id, content=text, chunk_size=600, overlap=120)
    return {"status": "queued", "result": result, "document_id": document_id}


# —— 会话（conversations）域：已迁移至 requirement_agent.api.routes.conversations（子批次 3.3.4）——
# 整块（只读 + 写 + finalize 连续）在原位置 include 子 router，保持注册顺序；
# 旧路径函数名继续可用（兼容转发，同一对象）。
from src.requirement_agent.api.routes.conversations import (  # noqa: E402,F401
    add_conversation_message,
    create_conversation,
    delete_conversation,
    finalize_conversation,
    get_conversation_messages,
    list_conversations,
    update_conversation,
)
from src.requirement_agent.api.routes.conversations import router as _conversations_router  # noqa: E402

router.include_router(_conversations_router)


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


# —— 审核工作台只读查询组：已迁移至 requirement_agent.api.routes.reviews（子批次 3.2.5）——
# 在原位置 include 子 router；旧路径函数名继续可用（兼容转发）。
# 注：POST /api/v1/reviews/submit 为写库/事务路由，仍保留在本文件，不迁移。
from src.requirement_agent.api.routes.reviews import (  # noqa: E402,F401
    get_review_detail,
    list_pending_reviews,
)
from src.requirement_agent.api.routes.reviews import router as _reviews_read_router  # noqa: E402

router.include_router(_reviews_read_router)


# —— 需求来源追踪组：已迁移至 requirement_agent.api.routes.sources（子批次 3.2.4）——
# 在原位置 include 子 router；旧路径函数名继续可用（兼容转发）。
from src.requirement_agent.api.routes.sources import get_source_trace  # noqa: E402,F401
from src.requirement_agent.api.routes.sources import router as _sources_router  # noqa: E402

router.include_router(_sources_router)


# —— 只读需求查询组：已迁移至 requirement_agent.api.routes.requirements（子批次 3.2.1）——
# 在本位置 include 子 router，保持路由注册顺序与 operationId 不变；旧路径函数名继续可用（兼容转发）。
from src.requirement_agent.api.routes.requirements import (  # noqa: E402,F401
    get_requirement_diff,
    get_requirement_trace,
    list_requirement_features,
    list_requirement_versions,
)
from src.requirement_agent.api.routes.requirements import router as _requirements_query_router  # noqa: E402

router.include_router(_requirements_query_router)


# —— 审计事件查询组：已迁移至 requirement_agent.api.routes.audit（子批次 3.2.4）——
# 在原位置 include 子 router；旧路径函数名继续可用（兼容转发）。
from src.requirement_agent.api.routes.audit import list_audit_events  # noqa: E402,F401
from src.requirement_agent.api.routes.audit import router as _audit_router  # noqa: E402

router.include_router(_audit_router)


# —— 审核提交（写库 / 强事务）：已迁移至 requirement_agent.api.routes.reviews（子批次 3.3.3）——
# 在原位置 include 子 router；旧路径函数名继续可用（兼容转发）。
# 注：强事务语义仍由 ReviewService.submit_decision 持有，未改。
from src.requirement_agent.api.routes.reviews import submit_review_decision  # noqa: E402,F401
from src.requirement_agent.api.routes.reviews import submit_router as _reviews_submit_router  # noqa: E402

router.include_router(_reviews_submit_router)