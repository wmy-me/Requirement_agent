from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.domain.requirement import RequirementSource
from src.infrastructure.db.session import check_database_connection
from src.infrastructure.db.seed_data import seed_requirement_master
from src.infrastructure.llm.openai_provider import LLMProvider
from src.interfaces.http.auth import AuthenticatedPrincipal, require_api_principal
from src.interfaces.http.schemas import (
    RequirementSubmitRequest,
    RequirementSubmitResponse,
    ReviewSubmitRequest,
)

router = APIRouter(tags=["requirements"])

requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()


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
        requester_id=payload.requester_id,
        requester_name=payload.requester_name,
        original_text=payload.original_text,
        metadata=payload.metadata,
    )
    response = requirement_service.submit_requirement(source)
    return RequirementSubmitResponse(
        message="Requirement submitted for review",
        source_type=payload.source_type,
        status=str(response["status"]),
        source_id=response.get("source_id"),
    )


@router.get("/api/v1/requirements/search", dependencies=[Depends(require_api_principal)])
async def search_requirements(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=20),
) -> dict[str, object]:
    return {"items": retrieval_service.search(q.strip(), limit=limit)}


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
