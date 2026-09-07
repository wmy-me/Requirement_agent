import json
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from src.agents.analyze_agent import AnalyzeAgent
from src.agents.extract_agent import ExtractAgent
from src.agents.risk_agent import RiskAgent
from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.domain.requirement import RequirementSource
from src.infrastructure.db.repositories import (
    AuditRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.parser.document_parser import DocumentParser
from src.infrastructure.storage.object_store import ObjectStorage
from src.infrastructure.db.session import check_database_connection
from src.infrastructure.db.seed_data import seed_requirement_master
from src.infrastructure.llm.openai_provider import LLMProvider
from src.interfaces.http.auth import AuthenticatedPrincipal, require_api_principal
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


@router.get("/")
async def root() -> dict[str, str]:
    return {"message": "Requirement Agent API", "status": "ok"}


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/health/db", dependencies=[Depends(require_api_principal)])
async def database_health() -> dict[str, object]:
    ok, _ = check_database_connection()
    return {"database": ok, "status": "ok" if ok else "unavailable"}


@router.get("/api/v1/health/llm", dependencies=[Depends(require_api_principal)])
async def llm_health() -> dict[str, object]:
    provider = LLMProvider()
    return {"configured": provider.is_configured(), "provider": provider.provider_name, "model": provider.model}


@router.post("/api/v1/admin/seed", dependencies=[Depends(require_api_principal)])
async def seed_demo_data() -> dict[str, object]:
    inserted = seed_requirement_master()
    return {"status": "ok", "inserted": inserted}


@router.get("/api/v1/requirements", dependencies=[Depends(require_api_principal)])
async def list_requirements() -> dict[str, list[dict[str, object]]]:
    return {"items": requirement_service.list_requirements()}


@router.post("/api/v1/requirements/submit", response_model=RequirementSubmitResponse)
async def submit_requirement(
    payload: RequirementSubmitRequest,
    _: AuthenticatedPrincipal = Depends(require_api_principal),
) -> RequirementSubmitResponse:
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
    _: AuthenticatedPrincipal = Depends(require_api_principal),
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


@router.get("/api/v1/requirements/search", dependencies=[Depends(require_api_principal)])
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


@router.post("/api/v1/agent/run", dependencies=[Depends(require_api_principal)])
async def run_agent_pipeline(payload: AgentRunRequest) -> dict[str, object]:
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
    review_required = bool(
        analysis_payload.get("duplicate")
        or analysis_payload.get("conflict")
        or any(
            risk_payload.get(key) == "high"
            for key in ("quality_risk", "change_risk", "technical_impact_risk")
        )
    )
    return {
        "status": "ok",
        "steps": ["extract", "retrieve", "analyze", "risk", "review_decision"],
        "extracted": extracted.model_dump(mode="python"),
        "candidates": candidates,
        "analysis": analysis_payload,
        "risk": risk_payload,
        "review_required": review_required,
        "next_action": "manual_review" if review_required else "can_commit",
    }


@router.post("/api/v1/agent/chat", dependencies=[Depends(require_api_principal)])
async def chat_with_agent(payload: AgentChatRequest) -> dict[str, object]:
    session_id = payload.session_id or uuid4().hex
    history = chat_sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": payload.message})

    run_text = payload.requirement_text or payload.message
    pipeline = await run_agent_pipeline(
        AgentRunRequest(
            source_type=payload.source_type,
            requester_name=payload.requester_name or "web-user",
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


@router.get("/api/v1/agent/chat/{session_id}", dependencies=[Depends(require_api_principal)])
async def get_agent_chat_history(session_id: str) -> dict[str, object]:
    return {"session_id": session_id, "history": chat_sessions.get(session_id, [])}


@router.get("/api/v1/reviews/pending", dependencies=[Depends(require_api_principal)])
async def list_pending_reviews(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    return {"items": source_repo.list_by_status("pending_review", limit=limit)}


@router.get("/api/v1/reviews/{source_id}/detail", dependencies=[Depends(require_api_principal)])
async def get_review_detail(source_id: int) -> dict[str, object]:
    detail = source_repo.get_detail(source_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review source not found")
    return detail


@router.get("/api/v1/sources/{source_id}/trace", dependencies=[Depends(require_api_principal)])
async def get_source_trace(source_id: int) -> dict[str, object]:
    trace = source_repo.get_trace(source_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return trace


@router.get("/api/v1/requirements/{requirement_key}/versions", dependencies=[Depends(require_api_principal)])
async def list_requirement_versions(requirement_key: str) -> dict[str, object]:
    return {"items": version_repo.list_by_requirement_key(requirement_key)}


@router.get("/api/v1/requirements/{requirement_key}/trace", dependencies=[Depends(require_api_principal)])
async def get_requirement_trace(requirement_key: str) -> dict[str, object]:
    trace = version_repo.trace_by_requirement_key(requirement_key)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")
    return trace


@router.get("/api/v1/audit/events", dependencies=[Depends(require_api_principal)])
async def list_audit_events(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
    return {"items": audit_repo.list_dicts(limit=limit)}


@router.post("/api/v1/reviews/submit")
async def submit_review_decision(
    payload: ReviewSubmitRequest,
    principal: AuthenticatedPrincipal = Depends(require_api_principal),
) -> dict[str, object]:
    try:
        return review_service.submit_decision(
            source_id=payload.source_id,
            decision=payload.decision,
            reviewer_id=principal.actor_id,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
            edited_requirement=payload.edited_requirement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
