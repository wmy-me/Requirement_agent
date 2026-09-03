from fastapi import APIRouter

router = APIRouter(tags=["requirements"])


@router.get("/")
async def root() -> dict[str, str]:
    return {"message": "Requirement Agent API", "status": "ok"}


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/requirements")
async def list_requirements() -> dict[str, list[str]]:
    return {"items": []}


@router.post("/api/v1/requirements/submit")
async def submit_requirement(payload: dict[str, str]) -> dict[str, str]:
    return {"message": "Requirement accepted", "source_type": payload.get("source_type", "unknown")}
