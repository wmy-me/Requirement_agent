from fastapi import APIRouter

from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.domain.requirement import RequirementSource
from src.interfaces.http.schemas import RequirementSubmitRequest, RequirementSubmitResponse

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


@router.get("/api/v1/requirements")
async def list_requirements() -> dict[str, list[dict[str, object]]]:
    return {"items": requirement_service.list_requirements()}


@router.post("/api/v1/requirements/submit", response_model=RequirementSubmitResponse)
async def submit_requirement(payload: RequirementSubmitRequest) -> RequirementSubmitResponse:
    source = RequirementSource(
        idempotency_key=payload.source_type + ":" + (payload.requester_id or "anonymous") + ":" + (payload.original_text or ""),
        source_type=payload.source_type,
        requester_id=payload.requester_id,
        requester_name=payload.requester_name,
        original_text=payload.original_text,
        metadata=payload.metadata,
    )
    response = requirement_service.submit_requirement(source)
    return RequirementSubmitResponse(
        message="Requirement accepted",
        source_type=payload.source_type,
        status=str(response["status"]),
    )


@router.get("/api/v1/requirements/search")
async def search_requirements(q: str, limit: int = 10) -> dict[str, object]:
    return {"items": retrieval_service.search(q, limit=limit)}


@router.post("/api/v1/reviews/submit")
async def submit_review_decision(payload: dict[str, str]) -> dict[str, str]:
    result = review_service.submit_decision(
        source_id=int(payload.get("source_id", "0")),
        decision=payload.get("decision", "approved"),
        reviewer_id=payload.get("reviewer_id", "system"),
        reviewer_name=payload.get("reviewer_name"),
        comment=payload.get("comment"),
        edited_requirement=payload.get("edited_requirement"),
    )
    return result
