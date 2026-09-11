"""审计事件查询 HTTP 路由（只读，子批次 3.2.4 迁移）。

来源：`src/interfaces/http/rest.py` 的 `GET /api/v1/audit/events`。
旧文件通过 `include_router` 在原位置复用本 router（同一实现，不重复注册）。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from requirement_agent.api.dependencies import audit_repo

router = APIRouter()


@router.get("/api/v1/audit/events")
async def list_audit_events(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
    """审计事件列表：按时间倒序分页返回操作日志 {"items": [...]}。"""
    return {"items": audit_repo.list_dicts(limit=limit)}
