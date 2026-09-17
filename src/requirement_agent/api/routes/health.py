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


@router.get("/api/v1/health/embedding")
async def embedding_health() -> dict[str, object]:
    """**库里的向量是不是当前模型算的**（B3.1 §4.6）。

    换 embedding 模型之后如果不重算存量向量，检索会拿新模型去算旧向量的余弦 ——
    pgvector 照常返回一个**看起来完全正常的分数**，没有任何地方会察觉两个向量
    根本不在同一个空间里。这个端点就是把那件事**说出来**。

    三种状态（见 `embedding_provenance` 的模块 docstring）：
      `ok`     —— 全部来自当前模型
      `stale`  —— 存在别的模型算的向量
      `unknown`—— 存在来源未知的存量（迁移 023 之前的行，**不等于**匹配）
      `empty`  —— 该表还没有向量

    `action` 只在不可信时给出，且是一条**可以直接复制执行**的命令 ——
    「该怎么办」不该让人再翻文档。
    """
    from requirement_agent.infrastructure.vector.embedding_provenance import (
        inspect_embedding_provenance,
        provenance_verdict,
    )

    trusted, reason = provenance_verdict()
    return {
        "trusted": trusted,
        "reason": reason,
        "tables": [
            {
                "table": report.table,
                "status": report.status,
                "total": report.total,
                "by_model": report.by_model,
                "unknown": report.unknown,
                "current_model": report.current_model,
            }
            for report in inspect_embedding_provenance()
        ],
        "action": None if trusted else "python -m scripts.reindex_embeddings",
    }


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
