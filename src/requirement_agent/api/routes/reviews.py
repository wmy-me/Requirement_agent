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

import logging

from fastapi import APIRouter, HTTPException, Query, status

from requirement_agent.config.settings import settings
from requirement_agent.api.dependencies import review_service, source_repo
from requirement_agent.api.schemas import ReviewSubmitRequest
from requirement_agent.infrastructure.db.repositories import ConcurrentModificationError

logger = logging.getLogger(__name__)

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


@router.get("/api/v1/reviews/{source_id}/merge-preview")
async def preview_merge_into_requirement(
    source_id: int,
    target_requirement_key: str = Query(min_length=1, max_length=80),
    merge_mode: str = Query(default="union", pattern="^(union|replace)$"),
) -> dict[str, object]:
    """预合并预览：把这条来源并进目标 REQ 会新增/修改/删除哪些功能。

    用 GET 是因为它**纯读、无副作用**（与同组的 `/detail` 一致）。返回的 add/modify/delete
    与真正提交后发生的完全一致 —— 预览与落库共用同一个 diff 内核。

    错误映射：来源不存在 / 目标 REQ 不存在 → 404；来源不在待审 → 409。
    """
    try:
        return review_service.preview_merge(
            source_id=source_id,
            target_requirement_key=target_requirement_key,
            merge_mode=merge_mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


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
            merge_mode=payload.merge_mode,
        )
    except ConcurrentModificationError as exc:
        # 乐观锁冲突：本次审核期间有人往同一个 REQ 提交过（合并或回滚）。
        # 整个事务已回滚，审核人看到的东西已不是最新版本 —— 必须重新加载再决定，
        # 不能照着旧预览点第二下（那会基于陈旧的功能集产生新版本）。
        logger.warning(
            "event=review_lock_conflict source_id=%s target=%s reason=%s",
            payload.source_id,
            payload.target_requirement_key,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该需求已被他人修改，请重新加载后再审核",
        ) from exc
    except ValueError as exc:
        # 409 的两个常见签名：「source_id=X not found」多为前端拿着已失效的 id（列表陈旧）；
        # 「source_id=X is not pending review」多为重复点击或该条已处理。
        # 只有前端 toast 看得到详情，服务端不留痕就没法排查——这里补上。
        logger.warning(
            "event=review_conflict source_id=%s decision=%s reason=%s",
            payload.source_id,
            payload.decision,
            exc,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
