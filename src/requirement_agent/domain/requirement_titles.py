"""从已有数据派生候选标题（纯函数，不调模型）。

**为什么不调模型**：同一个需求在不同人嘴里叫法不同，但这些叫法**本来就对应已有数据**——
发起人按业务对象叫、报表团队按能力叫、技术侧按能力+条件叫。所以直接派生即可，
既省一次调用，也避免模型每次换个说法造成漂移。

派生的标题一律是**提议**（`review_status='proposed'`），要人工确认才对外可见；
派生的写法也只是启发式，人若不满意可以自己加一条 `free` 视角的标题。
"""

from __future__ import annotations

from typing import Any

__all__ = ["derive_title_candidates"]

# 视角 → 允许的最大条数：同一视角派生的标题多了列表就成灾
_MAX_PER_ANGLE = 3


def _text(value: Any) -> str:
    return str(value or "").strip()


def derive_title_candidates(
    *,
    business_object: str = "",
    capabilities: list[dict[str, Any]] | None = None,
    constraint_keys: list[str] | None = None,
) -> list[dict[str, object]]:
    """从业务对象 / 能力 / 条件派生候选标题。

    三个视角对应三种叫法：

    | 视角 | 派生写法 | 例 |
    |---|---|---|
    | business_object | `{对象}` | 「员工数据」 |
    | capability | `{对象}{动作}能力` | 「Excel 导出能力」 |
    | constraint | `按条件的能力叫法` | 「按部门筛选导出」 |

    去重，且每个视角最多 `_MAX_PER_ANGLE` 条 —— 宁可少给，也不要让列表被
    同一视角的十几个近义标题淹掉。
    """
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    def _add(title: str, angle: str, **extra: Any) -> None:
        normalized = _text(title)
        if not normalized or normalized in seen:
            return
        if sum(1 for item in candidates if item["angle"] == angle) >= _MAX_PER_ANGLE:
            return
        seen.add(normalized)
        candidates.append({"title": normalized, "angle": angle, "source": "analysis", **extra})

    object_ = _text(business_object)
    if object_:
        _add(object_, "business_object")

    for capability in capabilities or []:
        if not isinstance(capability, dict):
            continue
        action, target = _text(capability.get("action")), _text(capability.get("object"))
        if not action or not target:
            continue
        _add(
            f"{target}{action}能力",
            "capability",
            capability_id=capability.get("capability_id"),
        )

    for key in constraint_keys or []:
        normalized = _text(key)
        if normalized:
            _add(normalized, "constraint", constraint_key=normalized)

    return candidates
