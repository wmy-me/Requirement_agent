"""需求相关 HTTP 路由 —— 只读查询组（子批次 3.2.1 迁移）。

来源：`src/interfaces/http/rest.py` 的只读需求查询路由。旧文件通过 `include_router`
复用本 router（同一实现，不重复注册）。路径 / method / response_model / tags 与原文件完全一致。

注意：本 router **不设 tags**，由父 router（`tags=["requirements"]`）在 include 时补齐，
避免 tags 重复。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.interfaces.http._state import feature_repo, retrieval_service, version_repo

router = APIRouter()


@router.get("/api/v1/requirements/{requirement_key}/versions")
async def list_requirement_versions(requirement_key: str) -> dict[str, object]:
    """需求版本列表：按 requirement_key 返回全部版本 {"items": [...]}。"""
    return {"items": version_repo.list_by_requirement_key(requirement_key)}


@router.get("/api/v1/requirements/{requirement_key}/features")
async def list_requirement_features(
    requirement_key: str,
    at_version: int | None = Query(default=None, ge=1),
    include_deleted: bool = Query(default=False),
) -> dict[str, object]:
    """需求特性列表：按 requirement_key（可选指定版本/是否含已删除）返回 {"items": [...]}。"""
    return {
        "items": feature_repo.list_by_requirement_key(
            requirement_key,
            at_version=at_version,
            include_deleted=include_deleted,
        )
    }


@router.get("/api/v1/requirements/{requirement_key}/diff")
async def get_requirement_diff(
    requirement_key: str,
    from_version: int | None = Query(default=None, ge=1),
    to_version: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """需求版本差异：对比 from_version 与 to_version 的字段差异；版本不存在返回 404。"""
    try:
        return feature_repo.diff_by_requirement_key(
            requirement_key,
            from_version=from_version,
            to_version=to_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/api/v1/requirements/{requirement_key}/trace")
async def get_requirement_trace(requirement_key: str) -> dict[str, object]:
    """需求溯源链路：按 requirement_key 返回版本演变轨迹；不存在返回 404。"""
    trace = version_repo.trace_by_requirement_key(requirement_key)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")
    return trace


# —— 相似需求检索组（子批次 3.2.2 迁移）——
# 独立 router：因为其原注册位置（/search、/features/search）早于上面的只读查询组，
# 拆成独立 router 以便 rest.py 在原位置 include，保持注册顺序不变。
search_router = APIRouter()


@search_router.get("/api/v1/requirements/search")
async def search_requirements(
    q: str = Query(default="", max_length=500),
    channel: str | None = Query(default=None, max_length=60),
    requester: str | None = Query(default=None, max_length=120),
    department: str | None = Query(default=None, max_length=120),
    business_domain: str | None = Query(default=None, max_length=120),
    sensitivity_level: str | None = Query(default=None, max_length=60),
    submitted_from: str | None = Query(default=None, max_length=40),
    submitted_to: str | None = Query(default=None, max_length=40),
    has_version_ge: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """多维检索需求：关键字 + 渠道/人/部门/领域/密级/时间/版本组合筛选。"""
    filters: dict[str, object] = {
        "channel": (channel or "").strip() or None,
        "requester": (requester or "").strip() or None,
        "department": (department or "").strip() or None,
        "business_domain": (business_domain or "").strip() or None,
        "sensitivity_level": (sensitivity_level or "").strip() or None,
        "submitted_from": (submitted_from or "").strip() or None,
        "submitted_to": (submitted_to or "").strip() or None,
        "has_version_ge": has_version_ge,
    }
    filters = {key: value for key, value in filters.items() if value not in (None, "")}
    rows = retrieval_service.search((q or "").strip(), limit=limit, filters=filters)
    return {"items": rows}


@search_router.get("/api/v1/requirements/features/search")
async def search_requirement_features(
    q: str = Query(default="", max_length=500),
    status: str | None = Query(default=None, max_length=60),
    requester: str | None = Query(default=None, max_length=120),
    has_version_ge: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """按功能条目检索：返回匹配 feature 行 {"items": [...]}。"""
    return {
        "items": retrieval_service.search_features(
            (q or "").strip(),
            status=status,
            requester=requester,
            has_version_ge=has_version_ge,
            limit=limit,
        )
    }
