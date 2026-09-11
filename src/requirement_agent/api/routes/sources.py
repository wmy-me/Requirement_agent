"""需求来源追踪 HTTP 路由（只读，子批次 3.2.4 迁移）。

来源：`src/interfaces/http/rest.py` 的 `GET /api/v1/sources/{source_id}/trace`。
旧文件通过 `include_router` 在原位置复用本 router（同一实现，不重复注册）。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.requirement_agent.api.dependencies import source_repo

router = APIRouter()


@router.get("/api/v1/sources/{source_id}/trace")
async def get_source_trace(source_id: int) -> dict[str, object]:
    """需求来源追踪：按 source_id 返回来源链路；不存在返回 404。"""
    trace = source_repo.get_trace(source_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return trace
