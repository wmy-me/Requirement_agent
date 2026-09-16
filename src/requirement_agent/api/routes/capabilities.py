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
from requirement_agent.api.schemas.capabilities import (
    CapabilityStatusRequest,
    ConstraintAliasRequest,
    FeatureCapabilityReviewRequest,
)
from requirement_agent.config.settings import settings

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


# ── 人工裁决（批次 5）────────────────────────────────────────────────────
#
# 这是「受控词表新增」与「能力匹配」两条职责边界的落地入口。
# 按方案 §11，**AI 没有任何自动通道能走到这里** —— proposed / pending_confirmation
# 只能由人翻成 confirmed / active。


@router.patch("/api/v1/feature-capabilities")
async def review_feature_capability(payload: FeatureCapabilityReviewRequest) -> dict[str, object]:
    """裁决「某条功能有没有某个能力」（方案 §11 B 级：人工一键确认）。

    只接受 `confirmed` / `dismissed`。**这是能力在需求上正式成立的唯一入口** ——
    在此之前关联一律是 `proposed`（AI 提议），搜索与展示都会如实标注。
    """
    updated = feature_capability_repo.update_status(
        feature_id=payload.feature_id,
        capability_id=payload.capability_id,
        status=payload.status,
        decided_by=settings.api_actor_id,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="feature capability link not found"
        )
    return updated


@router.patch("/api/v1/capabilities/{capability_id}")
async def update_capability_status(
    capability_id: int, payload: CapabilityStatusRequest
) -> dict[str, object]:
    """裁决能力本身：确认成立（`active`）/ 停用（`deprecated`）/ 打回提案态。

    `active` 之后该能力才参与 `find_exact` 的精确匹配 —— 也就是说，
    **AI 提议的新能力在被人确认之前，后续需求都匹配不到它**。
    """
    updated = capability_repo.update_status(capability_id, payload.status)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capability not found")
    return updated


@router.post("/api/v1/constraints/aliases")
async def add_constraint_alias(payload: ConstraintAliasRequest) -> dict[str, object]:
    """把一个原始表达登记为某条件的别名（人工四个选项之一）。

    另外三个选项（合并已有条件 / 新增正式条件 / 不结构化只留原文）分别对应
    直接沿用现有行、走条件词表、以及什么都不做 —— 都不需要新端点。
    """
    if constraint_repo.get(payload.constraint_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="constraint not found")
    return constraint_repo.add_alias(
        alias=payload.alias,
        constraint_id=payload.constraint_id,
        created_by=settings.api_actor_id or "review",
    )
