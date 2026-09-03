from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["requirements"])


@router.get("/requirements")
async def list_requirements() -> dict[str, list[str]]:
    return {"items": []}


@router.post("/requirements/submit")
async def submit_requirement(payload: dict[str, str]) -> dict[str, str]:
    return {"message": "Requirement accepted", "source_type": payload.get("source_type", "unknown")}
