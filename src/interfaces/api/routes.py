"""API routes for external requirement submission and search."""

from __future__ import annotations

from fastapi import APIRouter

from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.domain.requirement import RequirementSource
from src.interfaces.api.schemas import RequirementCreateRequest, RequirementCreateResponse

router = APIRouter(prefix="/api/v1", tags=["external-api"])

requirement_service = RequirementService()
retrieval_service = RetrievalService()


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/requirements", response_model=RequirementCreateResponse)
async def create_requirement(payload: RequirementCreateRequest) -> RequirementCreateResponse:
    source = RequirementSource(
        idempotency_key=f"{payload.source_type}:{payload.requester_id or 'anonymous'}:{payload.original_text or ''}",
        source_type=payload.source_type,
        requester_id=payload.requester_id,
        requester_name=payload.requester_name,
        original_text=payload.original_text,
        metadata=payload.metadata,
    )
    result = requirement_service.submit_requirement(source)
    return RequirementCreateResponse(
        requirement_key=str(result.get("requirement_key") or "REQ-000001"),
        status=str(result.get("status") or "accepted"),
    )


@router.get("/requirements/search")
async def search_requirements(q: str, limit: int = 10) -> dict[str, object]:
    return {"items": retrieval_service.search(q, limit=limit)}
