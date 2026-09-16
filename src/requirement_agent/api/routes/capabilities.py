"""能力与限定条件的受控词表路由（方案批次 1）。

**本批只提供只读查询。** 写入路径（Agent 提议 → 人工确认）属于批次 2 与批次 5，
届时应各自带 `proposed` / `pending_confirmation` 语义，不会在这里开一个能直接
创建 `active` 条目的口子 —— 这是方案 §11 职责边界的硬约束。

词表用途：
- 前端筛选下拉与详情展示；
- 批次 2 起注入抽取 prompt，让模型**从词表里选**而不是自由发挥（防漂移的关键）。

本模块 router 不设 tags，由父 router（`tags=["requirements"]`）在 include 时补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from requirement_agent.api.dependencies import (
    capability_repo,
    constraint_repo,
    feature_capability_repo,
)

router = APIRouter()


@router.get("/api/v1/capabilities")
async def list_capabilities(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(active|deprecated|pending_confirmation)$",
    ),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    """能力词表。`status=active` 只返回已确认的；不传则含待确认的提案。"""
    return {"items": capability_repo.list(status=status_filter, q=q, limit=limit)}


@router.get("/api/v1/capabilities/{capability_id}")
async def get_capability(capability_id: int) -> dict[str, object]:
    """单条能力；不存在返回 404。"""
    item = capability_repo.get(capability_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability not found")
    return item


@router.get("/api/v1/constraints")
async def list_constraints(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(active|deprecated|pending_confirmation)$",
    ),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    """限定条件词表（含各自的别名列表）。"""
    return {"items": constraint_repo.list(status=status_filter, q=q, limit=limit)}


@router.get("/api/v1/constraints/{constraint_id}")
async def get_constraint(constraint_id: int) -> dict[str, object]:
    """单条条件；不存在返回 404。"""
    item = constraint_repo.get(constraint_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="constraint not found")
    return item


@router.get("/api/v1/capabilities/{capability_id}/streams")
async def list_streams_by_capability(
    capability_id: int,
    constraint: str | None = Query(
        default=None, max_length=120, description="限定条件（正式键或原文均可）"
    ),
    review_status: str | None = Query(
        default=None,
        pattern="^(proposed|confirmed|dismissed)$",
        description="不传则不限状态；confirmed 才是人工确认过的可信档",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """按能力反查需求主线（方案 §6 的搜索 1 与 2）。

    - 只传 `capability_id`：命中所有具备该能力的需求主线
    - 再加 `constraint`：只命中**当前版本**同时带该条件的主线

    返回里带 `review_status`：关联默认是 `proposed`（AI 提议，尚未人工确认），
    调用方要自己决定要不要把 proposed 当成「有」。
    """
    if capability_repo.get(capability_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability not found")
    items = feature_capability_repo.search_streams(
        capability_id=capability_id,
        constraint_key=constraint,
        review_status=review_status,
        limit=limit,
    )
    return {"items": items}
