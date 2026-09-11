"""审核 HTTP 路由。

- 只读查询（子批次 3.2.5 迁移）：`GET /api/v1/reviews/pending`、`GET /api/v1/reviews/{source_id}/detail`。
- 审核提交（子批次 3.3.3 迁移）：`POST /api/v1/reviews/submit`（写库 / 强事务，经 `ReviewService`）。

来源：`src/interfaces/http/rest.py`。旧文件通过 `include_router` 在原位置复用本模块的**两个** router
（同一实现，不重复注册），并以兼容转发保留旧函数名（同一对象）。

为何拆两个 router：只读组与提交在原文件中的**注册位置不同**（只读组在中部、提交在末尾），
分成两个 router 分别 include 才能保持路由注册顺序不变。

注意：本模块 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.requirement_agent.config.settings import settings
from src.requirement_agent.api.dependencies import review_service, source_repo
from src.requirement_agent.api.schemas import ReviewSubmitRequest

router = APIRouter()         # 只读查询组
submit_router = APIRouter()  # 审核提交（写库 / 强事务）


@router.get("/api/v1/reviews/pending")
async def list_pending_reviews(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    """待人工评审列表：返回 pending_review 状态的需求 {"items": [...]}。"""
    return {"items": source_repo.list_by_status("pending_review", limit=limit)}


@router.get("/api/v1/reviews/{source_id}/detail")
async def get_review_detail(source_id: int) -> dict[str, object]:
    """评审详情：按 source_id 返回需求与相关分析的完整信息；不存在返回 404。"""
    detail = source_repo.get_detail(source_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review source not found")
    return detail


@submit_router.post("/api/v1/reviews/submit")
async def submit_review_decision(payload: ReviewSubmitRequest) -> dict[str, object]:
    """提交评审结论：记录决策并生成/更新需求与特性；冲突时返回 409。"""
    try:
        return review_service.submit_decision(
            source_id=payload.source_id,
            decision=payload.decision,
            reviewer_id=settings.api_actor_id,
            target_requirement_key=payload.target_requirement_key,
            reviewer_name=payload.reviewer_name,
            comment=payload.comment,
            edited_requirement=payload.edited_requirement,
            feature_overrides=payload.feature_overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
