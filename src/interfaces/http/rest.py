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


@router.get("/api/v1/health/db")
async def database_health() -> dict[str, object]:
    """数据库连通性检查：返回 {"database": bool, "status": ...}。"""
    ok, message = check_database_connection()
    return {"database": ok, "status": message}


@router.get("/api/v1/health/llm")
async def llm_health() -> dict[str, object]:
    """LLM 配置检查：返回是否配置及所用 provider / model。"""
    provider = LLMProvider()
    return {"configured": provider.is_configured(), "provider": provider.provider_name, "model": provider.model}


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


@router.get("/api/v1/requirements/search")
async def search_requirements(
    q: str = Query(default="", max_length=500),
    channel: str | None = Query(default=None, max_length=60),
    requester: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    business_domain: str | None = Query(default=None, max_length=120),
    sensitivity_level: str | None = Query(default=None, max_length=60),
    submitted_from: str | None = Query(default=None, max_length=40),
    submitted_to: str | None = Query(default=None, max_length=40),
    has_version_ge: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """多维检索需求：关键字 + 渠道/人/部门/领域/密级/时间/版本组合筛选。"""
    filters: dict[str, object] = {
        "channel": (channel or "").strip() or None,
        "requester": (requester or "").strip() or None,
        "department": (department or "").strip() or None,
        "business_domain": (business_domain or "").strip() or None,
        "sensitivity_level": (sensitivity_level or "").strip() or None,
        "submitted_from": (submitted_from or "").strip() or None,
        "submitted_to": (submitted_to or "").strip() or None,
        "has_version_ge": has_version_ge,
    }
    filters = {key: value for key, value in filters.items() if value not in (None, "")}
    rows = retrieval_service.search((q or "").strip(), limit=limit, filters=filters)
    return {"items": rows}


@router.get("/api/v1/requirements/features/search")
async def search_requirement_features(
    q: str = Query(default="", max_length=500),
    status: str | None = Query(default=None, max_length=60),
    requester: str | None = Query(default=None, max_length=120),
    has_version_ge: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """按功能条目检索：返回匹配 feature 行 {"items": [...]}。"""
    return {
        "items": retrieval_service.search_features(
            (q or "").strip(),
            status=status,
            requester=requester,
            has_version_ge=has_version_ge,
            limit=limit,
        )
    }


@router.get("/api/v1/documents")
async def list_documents(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    """文档资产列表：返回文档数组 {"items": [...]}。"""
    return {"items": document_repo.list_documents(limit=limit)}


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


@router.get("/api/v1/documents/search")
async def search_document_chunks(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict[str, object]:
    """按向量相似度检索文档分块：返回 {"items": [...]}。"""
    return {"items": document_repo.search_chunks(q.strip(), limit=limit)}


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


@router.get("/api/v1/reviews/pending")
async def list_pending_reviews(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    """待人工评审列表：返回 pending_review 状态的需求 {"items": [...]}。"""
    return {"items": source_repo.list_by_status("pending_review", limit=limit)}


@router.get("/api/v1/reviews/{source_id}/detail")
async def get_review_detail(source_id: int) -> dict[str, object]:
    """评审详情：按 source_id 返回需求与相关分析的完整信息；不存在返回 404。"""
    detail = source_repo.get_detail(source_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review source not found")
    return detail


@router.get("/api/v1/sources/{source_id}/trace")
async def get_source_trace(source_id: int) -> dict[str, object]:
    """需求来源追踪：按 source_id 返回来源链路；不存在返回 404。"""
    trace = source_repo.get_trace(source_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return trace


@router.get("/api/v1/requirements/{requirement_key}/versions")
async def list_requirement_versions(requirement_key: str) -> dict[str, object]:
    """需求版本列表：按 requirement_key 返回全部版本 {"items": [...]}。"""
    return {"items": version_repo.list_by_requirement_key(requirement_key)}


@router.get("/api/v1/requirements/{requirement_key}/features")
async def list_requirement_features(
    requirement_key: str,
    at_version: int | None = Query(default=None, ge=1),
    include_deleted: bool = Query(default=False),
) -> dict[str, object]:
    """需求特性列表：按 requirement_key（可选指定版本/是否含已删除）返回 {"items": [...]}。"""
    return {
        "items": feature_repo.list_by_requirement_key(
            requirement_key,
            at_version=at_version,
            include_deleted=include_deleted,
        )
    }


@router.get("/api/v1/requirements/{requirement_key}/diff")
async def get_requirement_diff(
    requirement_key: str,
    from_version: int | None = Query(default=None, ge=1),
    to_version: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """需求版本差异：对比 from_version 与 to_version 的字段差异；版本不存在返回 404。"""
    try:
        return feature_repo.diff_by_requirement_key(
            requirement_key,
            from_version=from_version,
            to_version=to_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/api/v1/requirements/{requirement_key}/trace")
async def get_requirement_trace(requirement_key: str) -> dict[str, object]:
    """需求溯源链路：按 requirement_key 返回版本演变轨迹；不存在返回 404。"""
    trace = version_repo.trace_by_requirement_key(requirement_key)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")
    return trace


@router.get("/api/v1/audit/events")
async def list_audit_events(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
    """审计事件列表：按时间倒序分页返回操作日志 {"items": [...]}。"""
    return {"items": audit_repo.list_dicts(limit=limit)}


@router.post("/api/v1/reviews/submit")
async def submit_review_decision(payload: ReviewSubmitRequest) -> dict[str, object]:
    """提交评审结论：记录决策并生成/更新需求与特性；冲突时返回 409。"""
    try:
        return review_service.submit_decision(
            source_id=payload.source_id,
            decision=payload.decision,
            reviewer_id=settings.api_actor_id,
            target_requirement_key=payload.target_requirement_key,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
            edited_requirement=payload.edited_requirement,
            feature_overrides=payload.feature_overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc