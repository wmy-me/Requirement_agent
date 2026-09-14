"""健康检查 HTTP 路由（只读，子批次 3.2.3 迁移）。

来源：`src/interfaces/http/rest.py` 的 `/api/v1/health/db` 与 `/api/v1/health/llm`。
旧文件通过 `include_router` 在原位置复用本 router（同一实现，不重复注册）。
路径 / method / response_model / tags 与原文件完全一致。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐，
保持 OpenAPI tags 与原行为一致。
"""

from __future__ import annotations

from fastapi import APIRouter

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.db.session import check_database_connection
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider, llm_call_stats

router = APIRouter()


@router.get("/api/v1/health/db")
async def database_health() -> dict[str, object]:
    """数据库连通性检查：返回 {"database": bool, "status": ...}。"""
    ok, message = check_database_connection()
    return {"database": ok, "status": message}


@router.get("/api/v1/health/llm")
async def llm_health() -> dict[str, object]:
    """LLM 配置与调用计量：chat/embedding 是否配置、请求调参、进程内计量快照。

    `stats` 是**进程内**累计值（每次调用递增，进程重启归零）。多 worker 部署时
    每个 worker 各有一份，取到的只是当前 worker 的视图。
    """
    provider = LLMProvider()
    return {
        "configured": provider.is_configured(),
        "provider": provider.provider_name,
        "model": provider.model,
        "embedding": {
            "configured": provider.embedding_configured(),
            "base_url": provider._embedding_base_url(),
            "model": provider.embedding_model,
            "dimension": settings.embedding_dimension,
        },
        "request": {
            "temperature": provider.temperature,
            "timeout_seconds": provider.timeout,
            "stream_read_timeout_seconds": provider.stream_read_timeout,
            "max_retries": provider.max_retries,
        },
        "stats": llm_call_stats(),
    }
