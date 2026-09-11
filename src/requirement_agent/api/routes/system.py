"""根与基础健康检查路由（子批次 3.3.5 迁移）。

来源：`src/interfaces/http/rest.py` 的 `GET /`（root）与 `GET /health`（healthcheck）。
旧文件通过 `include_router` 在原位置复用本 router。本 router 不设 tags，由父 router 补齐。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def root() -> dict[str, str]:
    """服务入口：返回 API 名称与运行状态。"""
    return {"message": "Requirement Agent API", "status": "ok"}


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    """基础存活探活：固定返回 {"status": "ok"}。"""
    return {"status": "ok"}
