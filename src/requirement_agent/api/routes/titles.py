"""需求候选标题路由（同一需求的不同视角入口）。

- 只读：`GET /api/v1/requirements/{requirement_key}/titles`
- 人工裁决：`PATCH /api/v1/requirement-titles/{title_id}`
- 手工新增：`POST /api/v1/requirements/{requirement_key}/titles`

**行上的 `highlight` 是这批标题的意义所在**：从不同标题点进去，内容不变、
高亮不同。它是从标题的锚点（能力 / 条件 / 业务对象）算出来的，不是另存的。

本模块 router 不设 tags，由父 router（`tags=["requirements"]`）补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from requirement_agent.api.dependencies import (
    feature_capability_repo,
    feature_repo,
    master_repo,
    title_repo,
    version_repo,
)
from requirement_agent.api.schemas.titles import (
    TitleCandidateCreateRequest,
    TitleCandidateReviewRequest,
)
from requirement_agent.config.settings import settings

router = APIRouter()


def _highlight_for(
    *,
    requirement_id: int,
    angle: str,
    capability_id: int | None,
    constraint_key: str | None,
    master_id: int,
) -> dict[str, object]:
    """算出「从这条标题点进去，该高亮哪几行功能」。

    - `capability`：关联到该能力的功能行（`feature_capability`，含 proposed ——
      高亮是给人看的线索，不该因为还没裁决就消失）
    - `constraint`：当前版本带该条件的**全部**功能行（条件修饰的是整条需求，
      不再细分到能力）
    - `business_object` / `free`：不给额外高亮，视为整条需求
    """
    if angle == "capability" and capability_id:
        links = feature_capability_repo.list_for_requirement(requirement_id)
        keys = [item["feature_key"] for item in links if item["capability_id"] == capability_id]
        return {"feature_keys": keys, "kind": "capability", "capability_id": capability_id}

    if angle == "constraint" and constraint_key:
        current = version_repo.get_current(master_id=master_id) or {}
        hit = any(
            (item.get("raw") == constraint_key or item.get("constraint_key") == constraint_key)
            for item in (current.get("constraint_snapshot") or [])
        )
        if not hit:
            return {"feature_keys": [], "kind": "constraint", "constraint_key": constraint_key}
        active = feature_repo.list_active(requirement_id)
        return {
            "feature_keys": [item["feature_key"] for item in active],
            "kind": "constraint",
            "constraint_key": constraint_key,
        }

    return {"feature_keys": [], "kind": angle}


@router.get("/api/v1/requirements/{requirement_key}/titles")
async def list_requirement_titles(
    requirement_key: str,
    review_status: str | None = None,
) -> dict[str, object]:
    """某条需求的候选标题，每条带 `highlight`（该视角该亮哪几行功能）。

    默认返回全部状态；传 `review_status=confirmed` 只看人工确认过的。
    """
    master = master_repo.get_by_key(requirement_key)
    if master is None or master.id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")

    items = title_repo.list_for_requirement(int(master.id), review_status=review_status)
    for item in items:
        item["highlight"] = _highlight_for(
            requirement_id=int(master.id),
            angle=str(item["angle"]),
            capability_id=item.get("capability_id"),
            constraint_key=item.get("constraint_key"),
            master_id=int(master.id),
        )
    return {"requirement_key": master.requirement_key, "items": items}


@router.patch("/api/v1/requirement-titles/{title_id}")
async def review_title_candidate(
    title_id: int, payload: TitleCandidateReviewRequest
) -> dict[str, object]:
    """裁决一条候选标题。**这是它对外可见（列表出多入口）的唯一入口。**"""
    updated = title_repo.update_status(
        title_id, payload.status, decided_by=settings.api_actor_id
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="title not found")
    return updated


@router.post("/api/v1/requirements/{requirement_key}/titles")
async def add_title_candidate(
    requirement_key: str, payload: TitleCandidateCreateRequest
) -> dict[str, object]:
    """人工新增一条候选标题（`angle=free`）。

    派生出的标题是启发式写法，人若不满意可以自己起一个 —— 这条路径补的就是
    「机器起不出好名字」那一格。新增同样是 `proposed`，确认后才对外可见。
    """
    master = master_repo.get_by_key(requirement_key)
    if master is None or master.id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="requirement not found")

    inserted = title_repo.upsert_many(
        requirement_id=int(master.id),
        titles=[
            {
                "title": payload.title,
                "angle": "free",
                "source": "review",
                "created_by": settings.api_actor_id or "review",
            }
        ],
    )
    items = title_repo.list_for_requirement(int(master.id))
    created = next((item for item in items if item["title"] == payload.title), None)
    return {"inserted": inserted, "item": created}
