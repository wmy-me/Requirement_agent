"""需求来源追踪 HTTP 路由（只读）。

来源：`src/interfaces/http/rest.py` 的 `GET /api/v1/sources/{source_id}/trace`。
旧文件通过 `include_router` 在原位置复用本 router（同一实现，不重复注册）。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from requirement_agent.api.dependencies import source_repo

router = APIRouter()


@router.get("/api/v1/sources/{source_id}/trace")
async def get_source_trace(source_id: int) -> dict[str, object]:
    """需求来源追踪：按 source_id 返回来源链路；不存在返回 404。"""
    trace = source_repo.get_trace(source_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return trace


@router.get("/api/v1/sources")
async def list_sources(
    status_filter: list[str] | None = Query(
        default=None,
        alias="status",
        description="按处理状态过滤，可重复传（如 ?status=received&status=failed）；不传 = 全部",
    ),
    source_type: str | None = Query(default=None, description="按渠道过滤（web / feishu …）"),
    requester: str | None = Query(default=None, description="按输入人过滤"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """**来源中心**的列表：最近提交的来源（新→旧）。

    此前只有 `GET /sources/{source_id}/trace`（按 id 溯源），**没有列表** ——
    来源中心那一屏做不出来。

    每项只带**摘要**（正文前 200 字、不含 `metadata`），详情走
    `/reviews/{source_id}/detail`。另带 `linked_requirement_key`：
    **这条来源最终变成了哪条需求**（没入库时为 `null`）。
    """
    return {
        "items": source_repo.list_sources(
            statuses=status_filter, source_type=source_type, requester=requester, limit=limit
        )
    }
