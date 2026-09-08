import asyncio
import json
from collections.abc import AsyncIterator
from queue import Queue
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.application.decision_rules import next_action_for as decision_next_action
from src.application.decision_rules import review_required as decision_review_required
from src.application.memory_service import MemoryContextBuilder, MemoryExtractor
from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.config.settings import settings
from src.domain.requirement import RequirementSource
from src.infrastructure.db.repositories import (
    AuditRepository,
    ChatRepository,
    DocumentAssetRepository,
    MemoryRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.db.seed_data import seed_requirement_master
from src.infrastructure.db.session import check_database_connection
from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.llm.openai_provider import LLMProvider
from src.infrastructure.parser.document_parser import DocumentParser
from src.infrastructure.storage.object_store import ObjectStorage
from src.infrastructure.worker.tasks import DocumentChunkingTask
from src.interfaces.http.schemas import (
    AgentChatRequest,
    AgentRunRequest,
    ConversationCreateRequest,
    ConversationMessageCreateRequest,
    ConversationUpdateRequest,
    RequirementSubmitRequest,
    RequirementSubmitResponse,
    ReviewSubmitRequest,
)

router = APIRouter(tags=["requirements"])

requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()
source_repo = RequirementSourceRepository()
version_repo = RequirementVersionRepository()
audit_repo = AuditRepository()
document_repo = DocumentAssetRepository()
chat_repo = ChatRepository()
memory_repo = MemoryRepository()
memory_context_builder = MemoryContextBuilder(memory_repo)
memory_extractor = MemoryExtractor(memory_repo)
embedding_service = EmbeddingService()
extract_agent = ExtractAgent()
analyze_agent = AnalyzeAgent()
risk_agent = RiskAgent()
document_parser = DocumentParser()
object_storage = ObjectStorage()
document_chunk_task = DocumentChunkingTask()
chat_sessions: dict[str, list[dict[str, object]]] = {}

RISK_KEYS = (
    ("质量", "quality_risk"),
    ("变更", "change_risk"),
    ("技术影响", "technical_impact_risk"),
)

NARRATIVE_SYSTEM_PROMPT = (
    "你是需求治理助手，正在与业务方对话。用户刚描述了一条需求，系统已完成结构化抽取、相似度检索、冲突与重复分析、风险评估，"
    "并给出了是否需要人工审核的判断（next_action：manual_review 或 can_commit）。"
    "请用第一人称、自然、清晰的中文，给出一段“最终结论”："
    "1) 先点明这条需求主要涉及哪个领域/方向；"
    "2) 结合检索/分析结果说明它与系统已有需求的关系：若命中相似项请直接点名 REQ 编号（如 REQ-000001）与判断（高度相似/关联/冲突）；没有则说明暂未发现重复；"
    "3) 给出“综合分析结果”要点（可用“- ”短列表）：重复/关联/冲突结论、需要澄清的点、风险评估结论；"
    "4) 结尾给出建议：manual_review 时建议补充澄清并进入人工评审，can_commit 时可作为独立需求继续处理。"
    "要求：总字数不超过 300 字；不要使用 markdown 标题、代码块或 JSON；不要罗列字段名；不要以反问句结尾。"
)


def _actor_id_or_default(actor_id: str | None) -> str:
    normalized = (actor_id or settings.api_actor_id or "api-user").strip()
    return normalized or "api-user"


def _summarize_text(text: str) -> str:
    """会话一句话摘要：LLM 优先，失败/未配置回退首段截断。"""
    snippet = " ".join((text or "").split())
    try:
        provider = LLMProvider()
        if provider.is_configured() and snippet:
            summary = provider.generate(
                f"请用一句话（不超过 80 字）概括下面这段对话的要点：\n{snippet[:2000]}",
                system_prompt="你是会话摘要器，只输出摘要本身。",
            )
            summary = (summary or "").strip()
            if summary:
                return summary[:200]
    except Exception:
        pass
    return snippet[:160] or "（空会话）"


def _sse(name: str, payload: object) -> str:
    """构造一行安全的 SSE 帧（payload 以 JSON 编码，避免原始换行破坏协议）。"""
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _fallback_narrative(pipeline: dict[str, object]) -> str:
    """无 LLM 或流式失败时的确定性中文总结，字段与前端卡片一致。"""
    extracted = pipeline.get("extracted") or {}
    analysis = pipeline.get("analysis") or {}
    risk = pipeline.get("risk") or {}
    candidates = analysis.get("candidates") or []

    title = extracted.get("requirement_title") or "这条需求"
    parts: list[str] = [f"我已经把你的需求理解成：「{title}」。"]
    summary = extracted.get("summary")
    if summary:
        parts.append(f"{summary}。")

    def _similarity(item: dict[str, object]) -> float:
        try:
            return float(item.get("similarity") or 0)
        except (TypeError, ValueError):
            return 0.0

    duplicates = [c for c in candidates if _similarity(c) >= 0.7]
    related = [c for c in candidates if 0.45 <= _similarity(c) < 0.7]
    if analysis.get("duplicate") and duplicates:
        keys = "、".join(str(c.get("requirement_key") or "") for c in duplicates[:3])
        parts.append(f"⚠️ 我发现 {len(duplicates)} 条高度相似的存量需求（{keys}），建议先核对是否重复。")
    elif analysis.get("related") and related:
        parts.append(f"我注意到 {len(related)} 条相关联需求，可能需要一起评估依赖关系。")
    elif candidates:
        parts.append("检索到了少量弱相关候选，但相似度不足以直接判断为相关或重复，建议人工确认。")
    if analysis.get("conflict"):
        parts.append("⚠️ 与现有权限或状态逻辑可能存在冲突，建议谨慎评审。")

    high = [label for label, key in RISK_KEYS if risk.get(key) == "high"]
    medium = [label for label, key in RISK_KEYS if risk.get(key) == "medium"]
    if high:
        parts.append(f"风险偏高：{'、'.join(high)}方向需要重点把关。")
    elif medium:
        parts.append(f"有中等风险项（{'、'.join(medium)}），建议评审时确认。")

    if not (analysis.get("duplicate") or analysis.get("conflict") or high):
        parts.append("整体没有发现明显的重复或高风险，可以先进入待办审核。")
    parts.append("需要我把这条正式提交进待办审核队列吗？")
    return "".join(parts)


def _narrative_prompt(pipeline: dict[str, object], memory_context: str | None = None) -> str:
    sections = {
        "抽取结果": pipeline.get("extracted"),
        "检索到的相似需求候选": pipeline.get("candidates"),
        "冲突/重复分析": pipeline.get("analysis"),
        "风险评估": pipeline.get("risk"),
    }
    blocks = [f"【{name}】\n{json.dumps(value, ensure_ascii=False, indent=2)}" for name, value in sections.items()]
    prompt = "请根据下面的结构化分析结果，给需求方一段口语化的总结。\n\n" + "\n\n".join(blocks)
    if memory_context:
        prompt += "\n\n【跨会话长期记忆，仅用于语气/上下文参考，不要逐字复述】\n" + memory_context
    return prompt


async def _narrative_chunks(pipeline: dict[str, object], memory_context: str | None = None) -> AsyncIterator[str]:
    """将结构化结论转成自然语言流。有 LLM 则走真实流式；否则退化为分段输出。"""
    provider = LLMProvider()
    if not provider.is_configured():
        # 未配置 LLM 时直接给出确定性结论（逐段输出以模拟节奏）
        text = _fallback_narrative(pipeline)
        for i in range(0, len(text), 10):
            yield text[i : i + 10]
        return

    # 生产线程写入线程安全的 stdlib Queue，事件循环经 asyncio.to_thread 读取，
    # 避免跨线程使用 asyncio.Queue（其唤醒语义不保证线程安全）。
    queue: Queue[tuple[str, str]] = Queue()

    def _pump() -> None:
        try:
            for chunk in provider.generate_stream(
                _narrative_prompt(pipeline, memory_context=memory_context), system_prompt=NARRATIVE_SYSTEM_PROMPT
            ):
                queue.put(("t", chunk))
            queue.put(("done", ""))
        except Exception as exc:  # pragma: no cover - depends on external LLM
            queue.put(("err", str(exc)))

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _pump)
    while True:
        kind, value = await asyncio.to_thread(queue.get)
        if kind == "t":
            yield value
            continue  # 继续读取下一个 token
        if kind == "done":
            return
        # kind == "err"：交给上层兜底处理
        raise RuntimeError(value)


async def _chat_stream_events(payload: AgentChatRequest) -> AsyncIterator[str]:
    """以 SSE 事件驱动完整 Agent 管线：状态 → 自然语言总结 → 结构化卡片。"""
    actor_id = _actor_id_or_default(payload.actor_id)
    session_id = payload.session_id or uuid4().hex
    conversation = chat_repo.get_conversation(session_id, actor_id=actor_id)
    if conversation is None:
        conversation = chat_repo.create_conversation(actor_id=actor_id, title="新对话")
        session_id = str(conversation["id"])
    client_message_id = payload.client_message_id or uuid4().hex
    history = chat_sessions.setdefault(session_id, [])

    # —— 已完成 run 重放：同一 client_message_id 重试/断连不再重算，直接回放落库结果 ——
    if payload.client_message_id:
        prior = chat_repo.get_run_by_client(session_id, payload.client_message_id)
        if prior is not None and prior.get("status") == "completed":
            assistant = chat_repo.get_assistant_message_for_run(str(prior["run_id"]))
            if assistant is not None:
                yield _sse("session", {"session_id": session_id, "run_id": str(prior["run_id"])})
                if assistant.get("artifacts"):
                    yield _sse("artifacts", {"artifacts": assistant["artifacts"]})
                content = str(assistant.get("content") or "已完成分析。")
                for i in range(0, len(content), 10):
                    yield _sse("narrative", {"t": content[i : i + 10]})
                yield _sse("done", {"run_id": str(prior["run_id"])})
                return

    history.append({"role": "user", "content": payload.message, "client_message_id": client_message_id})

    user_message = chat_repo.upsert_user_message(
        conversation_id=session_id,
        content=payload.message,
        actor_id=actor_id,
        client_message_id=client_message_id,
    )
    run = chat_repo.create_run(
        conversation_id=session_id,
        client_message_id=client_message_id,
        status="running",
        meta={"source_type": payload.source_type, "requester_name": payload.requester_name},
    )
    run_id = str(run["run_id"])

    run_text = payload.requirement_text or payload.message
    pipeline: dict[str, object] = {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "source_type": payload.source_type,
        "requester_name": payload.requester_name,
        "analysis_mode": payload.analysis_mode,
    }
    narrative_parts: list[str] = []

    try:
        yield _sse("session", {"session_id": session_id, "run_id": run_id})

        yield _sse("step", {"step": "extract", "label": "正在理解你的需求…"})
        extracted = await run_in_threadpool(
            extract_agent.extract,
            run_text,
            source_type=payload.source_type,
            requester_name=payload.requester_name,
        )
        extracted_payload = extracted.model_dump(mode="python")
        pipeline["extracted"] = extracted_payload

        yield _sse("step", {"step": "retrieve", "label": "正在检索相似需求…"})
        candidates = await run_in_threadpool(
            retrieval_service.search,
            extracted_payload.get("summary") or run_text,
            limit=5,
        )
        pipeline["candidates"] = candidates

        yield _sse("step", {"step": "analyze", "label": "正在分析冲突与重复…"})
        analysis = await run_in_threadpool(analyze_agent.analyze, extracted, candidates)
        analysis_payload = analysis.model_dump(mode="python")
        pipeline["analysis"] = analysis_payload
        pipeline["analysis"]["candidates"] = [
            {
                **candidate,
                "evidence": candidate.get("evidence") or [],
                "score_label": "高" if float(candidate.get("similarity") or 0) >= 0.7 else "中" if float(candidate.get("similarity") or 0) >= 0.45 else "低",
            }
            for candidate in pipeline["analysis"].get("candidates") or []
        ]

        yield _sse("step", {"step": "risk", "label": "正在评估风险…"})
        risk = await run_in_threadpool(risk_agent.assess, extracted)
        risk_payload = risk.model_dump(mode="python")
        pipeline["risk"] = risk_payload

        pipeline["risk"]["confidence"] = round(float(pipeline["risk"].get("confidence") or 0.0), 2)

        pipeline["review_required"] = decision_review_required(analysis_payload, risk_payload)
        pipeline["next_action"] = decision_next_action(analysis_payload, risk_payload)

        # 跨会话长期记忆：仅当命中才注入最终结论 prompt
        memory_ctx = memory_context_builder.build_context(actor_id, run_text, limit=4)

        yield _sse("artifacts", {"artifacts": pipeline})

        yield _sse("narrative", {"start": True})
        try:
            async for token in _narrative_chunks(pipeline, memory_context=memory_ctx):
                narrative_parts.append(token)
                yield _sse("narrative", {"t": token})
        except Exception:
            pass
        if not narrative_parts:
            fallback_text = _fallback_narrative(pipeline)
            for i in range(0, len(fallback_text), 10):
                piece = fallback_text[i : i + 10]
                narrative_parts.append(piece)
                yield _sse("narrative", {"t": piece})

        assistant_content = "".join(narrative_parts)
        assistant_message: dict[str, object] = {
            "role": "assistant",
            "content": assistant_content,
            "artifacts": pipeline,
        }
        history.append(assistant_message)
        chat_repo.append_assistant_message(
            conversation_id=session_id,
            content=assistant_content,
            artifacts=pipeline,
            run_id=run_id,
        )
        chat_repo.update_run(run_id=run_id, status="completed", meta={"conversation_id": session_id, "assistant_message_id": user_message["id"]})
        yield _sse("done", {"run_id": run_id})
    except asyncio.CancelledError:
        chat_repo.update_run(run_id=run_id, status="cancelled", error="cancelled by client")
        raise
    except Exception as exc:
        error_text = f"分析遇到问题：{exc}"
        history.append({"role": "assistant", "content": error_text, "artifacts": None})
        chat_repo.update_run(run_id=run_id, status="failed", error=str(exc), meta={"conversation_id": session_id})
        yield _sse("error", {"message": str(exc)})
        yield _sse("done", {"run_id": run_id})


@router.get("/")
async def root() -> dict[str, str]:
    return {"message": "Requirement Agent API", "status": "ok"}


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/health/db")
async def database_health() -> dict[str, object]:
    ok, _ = check_database_connection()
    return {"database": ok, "status": "ok" if ok else "unavailable"}


@router.get("/api/v1/health/llm")
async def llm_health() -> dict[str, object]:
    provider = LLMProvider()
    return {"configured": provider.is_configured(), "provider": provider.provider_name, "model": provider.model}


@router.post("/api/v1/admin/seed")
async def seed_demo_data() -> dict[str, object]:
    inserted = seed_requirement_master()
    return {"status": "ok", "inserted": inserted}


@router.get("/api/v1/requirements")
async def list_requirements() -> dict[str, list[dict[str, object]]]:
    return {"items": requirement_service.list_requirements()}


@router.post("/api/v1/requirements/submit", response_model=RequirementSubmitResponse)
async def submit_requirement(payload: RequirementSubmitRequest) -> RequirementSubmitResponse:
    source = RequirementSource(
        idempotency_key=payload.source_type + ":" + (payload.requester_id or "anonymous") + ":" + payload.original_text,
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
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=20),
    channel: str | None = Query(default=None, max_length=60),
    department: str | None = Query(default=None, max_length=120),
    business_domain: str | None = Query(default=None, max_length=120),
    sensitivity_level: str | None = Query(default=None, max_length=60),
    submitted_from: str | None = Query(default=None, max_length=40),
    submitted_to: str | None = Query(default=None, max_length=40),
) -> dict[str, object]:
    filters = {
        "channel": (channel or "").strip() or None,
        "department": (department or "").strip() or None,
        "business_domain": (business_domain or "").strip() or None,
        "sensitivity_level": (sensitivity_level or "").strip() or None,
        "submitted_from": (submitted_from or "").strip() or None,
        "submitted_to": (submitted_to or "").strip() or None,
    }
    return {"items": retrieval_service.search(q.strip(), limit=limit, filters=filters)}


@router.get("/api/v1/documents")
async def list_documents(limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    return {"items": document_repo.list_documents(limit=limit)}


@router.get("/api/v1/documents/{document_id}")
async def get_document(document_id: int) -> dict[str, object]:
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.get("/api/v1/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: int,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, object]:
    document = document_repo.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return {"document_id": document_id, "items": document_repo.get_chunks(document_id, limit=limit)}


@router.post("/api/v1/documents/{document_id}/reindex")
async def reindex_document_chunks(document_id: int) -> dict[str, object]:
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
    return {"items": document_repo.search_chunks(q.strip(), limit=limit)}


@router.post("/api/v1/agent/run")
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
    """与 /chat/stream 共享的分析管线（非流式，供回放/兼容）。"""
    extracted = extract_agent.extract(
        payload.original_text,
        source_type=payload.source_type,
        requester_name=payload.requester_name,
    )
    candidates = retrieval_service.search(extracted.summary or payload.original_text, limit=5)
    analysis = analyze_agent.analyze(extracted, candidates)
    risk = risk_agent.assess(extracted)
    risk_payload = risk.model_dump(mode="python")
    analysis_payload = analysis.model_dump(mode="python")
    return {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "extracted": extracted.model_dump(mode="python"),
        "candidates": candidates,
        "analysis": analysis_payload,
        "risk": risk_payload,
        "review_required": decision_review_required(analysis_payload, risk_payload),
        "next_action": decision_next_action(analysis_payload, risk_payload),
    }


@router.post("/api/v1/agent/chat")
async def chat_with_agent(payload: AgentChatRequest) -> dict[str, object]:
    actor_id = _actor_id_or_default(payload.actor_id)
    session_id = payload.session_id or str(uuid4())
    conversation = chat_repo.get_conversation(session_id, actor_id=actor_id)
    if conversation is None:
        conversation = chat_repo.create_conversation(actor_id=actor_id, title="新对话")
        session_id = str(conversation["id"])

    history = chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": payload.message})

    run_text = payload.requirement_text or payload.message
    pipeline = await run_agent_pipeline(
        AgentRunRequest(
            source_type=payload.source_type,
            requester_name=payload.requester_name,
            original_text=run_text,
        )
    )
    extracted = pipeline["extracted"]
    analysis = pipeline["analysis"]
    risk = pipeline["risk"]
    answer = (
        f"已完成 Agent 分析：标题为「{extracted.get('requirement_title')}」，"
        f"重复={analysis.get('duplicate')}，冲突={analysis.get('conflict')}，"
        f"质量风险={risk.get('quality_risk')}，变更风险={risk.get('change_risk')}。"
    )
    assistant_message = {"role": "assistant", "content": answer, "artifacts": pipeline}
    history.append(assistant_message)

    client_message_id = payload.client_message_id or str(uuid4())
    chat_repo.upsert_user_message(
        conversation_id=session_id,
        content=payload.message,
        actor_id=actor_id,
        client_message_id=client_message_id,
    )
    run = chat_repo.create_run(conversation_id=session_id, client_message_id=client_message_id, status="completed")
    chat_repo.append_assistant_message(
        conversation_id=session_id,
        content=answer,
        artifacts=pipeline,
        run_id=str(run["run_id"]),
    )
    return {"session_id": session_id, "message": assistant_message, "history": chat_repo.get_messages(session_id)}


@router.post("/api/v1/agent/chat/stream")
async def stream_agent_chat(payload: AgentChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_stream_events(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/v1/agent/chat/{session_id}")
async def get_agent_chat_history(session_id: str) -> dict[str, object]:
    history = chat_repo.get_messages(session_id)
    return {"session_id": session_id, "history": history or chat_sessions.get(session_id, [])}


@router.get("/api/v1/conversations")
async def list_conversations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    normalized_actor_id = _actor_id_or_default(actor_id)
    return {"items": chat_repo.list_conversations(actor_id=normalized_actor_id, limit=limit, offset=offset)}


@router.post("/api/v1/conversations")
async def create_conversation(payload: ConversationCreateRequest) -> dict[str, object]:
    actor_id = _actor_id_or_default(payload.actor_id)
    conversation = chat_repo.create_conversation(actor_id=actor_id, title=payload.title)
    return conversation


@router.get("/api/v1/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    normalized_actor_id = _actor_id_or_default(actor_id)
    conversation = chat_repo.get_conversation(conversation_id, actor_id=normalized_actor_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"conversation_id": conversation_id, "items": chat_repo.get_messages(conversation_id)}


@router.post("/api/v1/conversations/{conversation_id}/messages")
async def add_conversation_message(conversation_id: str, payload: ConversationMessageCreateRequest) -> dict[str, object]:
    actor_id = _actor_id_or_default(payload.actor_id)
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
    normalized_actor_id = _actor_id_or_default(actor_id)
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
    normalized_actor_id = _actor_id_or_default(actor_id)
    deleted = chat_repo.delete_conversation(conversation_id, actor_id=normalized_actor_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/api/v1/conversations/{conversation_id}/finalize")
async def finalize_conversation(conversation_id: str, actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    """会话收尾：生成一句话摘要 + 触发长期记忆抽取。"""
    normalized_actor_id = _actor_id_or_default(actor_id)
    conversation = chat_repo.get_conversation(conversation_id, actor_id=normalized_actor_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    messages = chat_repo.get_messages(conversation_id)
    transcript = "\n".join(str(msg["content"] or "") for msg in messages if msg["role"] in {"user", "assistant"})
    summary = _summarize_text(transcript)
    chat_repo.update_conversation(conversation_id, summary=summary, actor_id=normalized_actor_id)
    memory = memory_extractor.extract_from_conversation(
        actor_id=normalized_actor_id,
        conversation_id=conversation_id,
        messages=messages,
        existing_notes=memory_repo.list_memories(actor_id=normalized_actor_id, limit=50),
    )
    return {"status": "finalized", "conversation_id": conversation_id, "summary": summary, "memory": memory}


@router.get("/api/v1/agent/runs/{run_id}")
async def get_agent_run(run_id: str) -> dict[str, object]:
    run = chat_repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent run not found")
    return run


@router.get("/api/v1/memory")
async def list_memory(actor_id: str | None = Query(default=None, max_length=120), limit: int = Query(default=20, ge=1, le=50)) -> dict[str, object]:
    normalized_actor_id = _actor_id_or_default(actor_id)
    return {"items": memory_repo.list_memories(actor_id=normalized_actor_id, limit=limit)}


@router.post("/api/v1/memory")
async def upsert_memory(payload: dict[str, object]) -> dict[str, object]:
    actor_id = _actor_id_or_default(str(payload.get("actor_id") or settings.api_actor_id or "api-user"))
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
    normalized_actor_id = _actor_id_or_default(actor_id)
    note = memory_repo.update_status(memory_id, "deleted")
    if note is None or note["actor_id"] != normalized_actor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    return {"status": "deleted", "memory": note}


@router.get("/api/v1/memory/context")
async def get_memory_context(query: str = Query(min_length=1, max_length=200), actor_id: str | None = Query(default=None, max_length=120)) -> dict[str, object]:
    normalized_actor_id = _actor_id_or_default(actor_id)
    return {"context": memory_context_builder.build_context(normalized_actor_id, query, limit=4)}


@router.get("/api/v1/reviews/pending")
async def list_pending_reviews(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    return {"items": source_repo.list_by_status("pending_review", limit=limit)}


@router.get("/api/v1/reviews/{source_id}/detail")
async def get_review_detail(source_id: int) -> dict[str, object]:
    detail = source_repo.get_detail(source_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review source not found")
    return detail


@router.get("/api/v1/sources/{source_id}/trace")
async def get_source_trace(source_id: int) -> dict[str, object]:
    trace = source_repo.get_trace(source_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return trace


@router.get("/api/v1/requirements/{requirement_key}/versions")
async def list_requirement_versions(requirement_key: str) -> dict[str, object]:
    return {"items": version_repo.list_by_requirement_key(requirement_key)}


@router.get("/api/v1/requirements/{requirement_key}/trace")
async def get_requirement_trace(requirement_key: str) -> dict[str, object]:
    trace = version_repo.trace_by_requirement_key(requirement_key)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")
    return trace


@router.get("/api/v1/audit/events")
async def list_audit_events(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
    return {"items": audit_repo.list_dicts(limit=limit)}


@router.post("/api/v1/reviews/submit")
async def submit_review_decision(payload: ReviewSubmitRequest) -> dict[str, object]:
    try:
        return review_service.submit_decision(
            source_id=payload.source_id,
            decision=payload.decision,
            reviewer_id=settings.api_actor_id,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
            edited_requirement=payload.edited_requirement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
