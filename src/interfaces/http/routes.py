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
from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.config.settings import settings
from src.domain.requirement import RequirementSource
from src.infrastructure.db.repositories import (
    AuditRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.db.seed_data import seed_requirement_master
from src.infrastructure.db.session import check_database_connection
from src.infrastructure.llm.openai_provider import LLMProvider
from src.infrastructure.parser.document_parser import DocumentParser
from src.infrastructure.storage.object_store import ObjectStorage
from src.interfaces.http.schemas import (
    AgentChatRequest,
    AgentRunRequest,
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
extract_agent = ExtractAgent()
analyze_agent = AnalyzeAgent()
risk_agent = RiskAgent()
document_parser = DocumentParser()
object_storage = ObjectStorage()
chat_sessions: dict[str, list[dict[str, object]]] = {}

RISK_KEYS = (
    ("质量", "quality_risk"),
    ("变更", "change_risk"),
    ("技术影响", "technical_impact_risk"),
)

NARRATIVE_SYSTEM_PROMPT = (
    "你是需求治理助手，正在与业务方对话。用户刚描述了一条业务需求，系统已完成结构化抽取、相似度检索、冲突分析和风险评估。"
    "请用第一人称、口语化、亲切的中文，向用户总结你对这条需求的理解、发现的相似/重复项、风险结论，并给出下一步建议。"
    "要求：总字数不超过 250 字；不要使用 markdown 标题、编号或代码块；不要罗列 JSON 字段；结尾自然地询问是否需要提交审核。"
)


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

    duplicates = [c for c in candidates if _similarity(c) >= 0.8]
    related = [c for c in candidates if 0.6 <= _similarity(c) < 0.8]
    if analysis.get("duplicate") and duplicates:
        keys = "、".join(str(c.get("requirement_key") or "") for c in duplicates[:3])
        parts.append(f"⚠️ 我发现 {len(duplicates)} 条高度相似的存量需求（{keys}），建议先核对是否重复。")
    elif analysis.get("related") and related:
        parts.append(f"我注意到 {len(related)} 条相关联需求，可能需要一起评估依赖关系。")
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


def _narrative_prompt(pipeline: dict[str, object]) -> str:
    sections = {
        "抽取结果": pipeline.get("extracted"),
        "检索到的相似需求候选": pipeline.get("candidates"),
        "冲突/重复分析": pipeline.get("analysis"),
        "风险评估": pipeline.get("risk"),
    }
    blocks = [f"【{name}】\n{json.dumps(value, ensure_ascii=False, indent=2)}" for name, value in sections.items()]
    return "请根据下面的结构化分析结果，给需求方一段口语化的总结。\n\n" + "\n\n".join(blocks)


async def _narrative_chunks(pipeline: dict[str, object]) -> AsyncIterator[str]:
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
                _narrative_prompt(pipeline), system_prompt=NARRATIVE_SYSTEM_PROMPT
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
    session_id = payload.session_id or uuid4().hex
    history = chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": payload.message})

    run_text = payload.requirement_text or payload.message
    pipeline: dict[str, object] = {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "source_type": payload.source_type,
        "requester_name": payload.requester_name,
    }
    narrative_parts: list[str] = []

    try:
        yield _sse("session", {"session_id": session_id})

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

        yield _sse("step", {"step": "risk", "label": "正在评估风险…"})
        risk = await run_in_threadpool(risk_agent.assess, extracted)
        risk_payload = risk.model_dump(mode="python")
        pipeline["risk"] = risk_payload

        pipeline["review_required"] = decision_review_required(analysis_payload, risk_payload)
        pipeline["next_action"] = decision_next_action(analysis_payload, risk_payload)

        yield _sse("narrative", {"start": True})
        try:
            async for token in _narrative_chunks(pipeline):
                narrative_parts.append(token)
                yield _sse("narrative", {"t": token})
        except Exception:
            # 流式总结失败不致命：若已无内容则回退到确定性结论
            pass
        if not narrative_parts:
            fallback_text = _fallback_narrative(pipeline)
            for i in range(0, len(fallback_text), 10):
                piece = fallback_text[i : i + 10]
                narrative_parts.append(piece)
                yield _sse("narrative", {"t": piece})

        assistant_message: dict[str, object] = {
            "role": "assistant",
            "content": "".join(narrative_parts),
            "artifacts": pipeline,
        }
        history.append(assistant_message)
        yield _sse("artifacts", {"artifacts": pipeline})
        yield _sse("done", {})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error_text = f"分析遇到问题：{exc}"
        history.append({"role": "assistant", "content": error_text, "artifacts": None})
        yield _sse("error", {"message": str(exc)})
        yield _sse("done", {})


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
    session_id = payload.session_id or uuid4().hex
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
    return {"session_id": session_id, "message": assistant_message, "history": history}


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
    return {"session_id": session_id, "history": chat_sessions.get(session_id, [])}


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
