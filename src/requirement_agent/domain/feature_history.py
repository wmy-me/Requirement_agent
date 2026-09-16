"""版本时刻的功能集复原（纯函数：不接 session、不写库、不读配置）。

**为什么要有这个模块。** `requirement_feature` 是「跨版本存活的行」，`content` 是
**就地覆写**的（`repositories/review.py` 的 `modify` 分支只有 `UPDATE ... SET content`），
旧文字不另存。所以 `list_by_requirement_key(at_version=N)` 原先只复原了
**「哪些功能存在」**，`content` / `module_*` 拿到的却是**今天的值** ——
一个在 v3 被改过措辞的功能，查 v1 会返回 v3 之后的文字。

这有两个后果：`/diff` 的 `modified` **结构上永远为空**（两端取的是同一条当前值），
以及 revert 会变成「回滚了成员、没回滚内容」。

**复原办法**：`requirement_version.feature_changes` 是逐版本的差量记录，把它**反向回放**
就能从今天回到任意历史时刻。三条逆操作（与 `commit_nodes` / `sync_features` 的写入形状
一一对应）：

| 记录 op | 逆操作 |
|---|---|
| `add` | 该 key 在这一版才诞生 → 置为不存在 |
| `modify` | 内容回到 `before`；带 `module_before` 时模块一并回 |
| `delete` | 该 key 当时还在 → 复活，内容取记录里的 `content` |

**两条容易搞错、已写死的规则：**

1. **按版本号降序回放。** 晚发生的变更先被撤销，链才自洽。同一版本内也逆序
   （一条 `feature_changes` 是顺序施加的，逆操作就该逆序）。
2. **锚点是「当前全部行」，不是「当前 active 行」。** 软删的行必须留在锚点里 ——
   某个 key 在目标版本之后被删掉时，正是靠它的锚点状态被 `delete` 的逆操作复活。

**已知限制（如实写出，不假装完备）：**

- `module_before` 只记了模块 **key** 没记 name（写入侧存的是 `item.module_before[0]`），
  所以复原模块时 name 会退化成 key。全仓约定 `module_name` 缺失即回落 `module_key`，
  绝大多数数据下无损，但 name 与 key 不同的数据会看出差别。
- `apply_overrides` 产出的 modify **不记模块**，且「模块变了但内容没变」走 `keep`
  **不产出任何记录** —— 这两类模块变化**不可复原**（保持当前值）。
- 历史里出现表里没有的 `feature_key` 时不抛异常（防御），该行按记录本身复原，
  ordinal 取 0。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# 同一包的兄弟模块：模块标签归一的口径必须只有一份（两份实现迟早分叉）。
from requirement_agent.domain.feature_diff import _normalize_module

__all__ = [
    "FeatureState",
    "PlannedRevertRow",
    "reconstruct_features_at_version",
    "plan_revert",
]

RevertOp = Literal["keep", "modify", "add", "delete"]


@dataclass(frozen=True, slots=True)
class FeatureState:
    """某条 feature 在**目标版本时刻**的样子。"""

    feature_key: str
    content: str
    ordinal: int
    module_key: str | None = None
    module_name: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedRevertRow:
    """一条 feature 在一次回滚里的归宿。

    **按 `feature_key` 直接对齐，不做内容匹配** —— 回滚的目标集合由
    `reconstruct_features_at_version` 算出，每条的身份是已知的，没有「猜谁是谁」的空间。

    `op` 描述的是**相对上一版（即当前）**的关系，与 `feature_changes` 的取值域一致：
    当前没有该 key 而目标有 → `add`；两边都有且内容不同 → `modify`；内容相同 → `keep`；
    当前有而目标没有 → `delete`。

    `revived=True` 表示这条 `add` 命中了一行**当前已软删**的行（走 UPDATE 复活，
    不新建行）—— 复活保住 `feature_key` 与挂在 `feature_id` 上的能力关联。
    """

    op: RevertOp
    feature_key: str
    content: str
    ordinal: int
    module_key: str | None
    module_name: str | None
    row_id: int | None = None
    before: str | None = None
    module_before: tuple[str | None, str | None] | None = None
    revived: bool = False


def _is_alive(row: Mapping[str, Any]) -> bool:
    """当前行是否生效。

    两个条件都要：`status = 'active'`（与 `list_active` 一致）**且**
    `removed_version_no IS NULL`（与 `at_version` 版本窗口的判据一致）。单看一个会与
    另一边不一致 —— 宁可严一点，让复原点与它替代的那条 SQL 取同一套语义。
    """
    if str(row.get("status") or "active").strip().lower() != "active":
        return False
    return row.get("removed_version_no") is None


def _feature_key_of(value: Any) -> str:
    return str(value or "").strip()


def reconstruct_features_at_version(
    current_rows: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    *,
    target_version: int,
) -> list[FeatureState]:
    """把当前的功能行沿 `feature_changes` 反向回放到 `target_version` 时刻。

    - `current_rows`：某 REQ 的**全部**行（含已软删），形状同
      `RequirementFeatureRepository.list_by_requirement_key(include_deleted=True)`。
    - `history`：`[{"version_no": int, "feature_changes": [...]}, ...]`。**可以传全部版本**——
      函数只回放 `version_no > target_version` 的那些，**目标版本自己那一版不撤销**
      （它的变更正是「让它成为目标版本」的原因）。顺序无关，内部按版本号降序排。
    - 返回：目标版本时刻**存活**的行，按 `(ordinal, feature_key)` 升序 ——
      与「当时按 ordinal 列出来」的顺序一致（匹配行保持原 ordinal、新增行追加到末尾，
      ordinal 只增不减）。

    `target_version` 等于当前版本时恒等于「当前生效集合」，可作为不变式使用。
    """
    state: dict[str, dict[str, Any]] = {}
    for row in current_rows:
        key = _feature_key_of(row.get("feature_key"))
        if not key:
            continue
        state[key] = {
            "content": str(row.get("content") or ""),
            "module_key": _normalize_module(row.get("module_key")),
            "module_name": _normalize_module(row.get("module_name")),
            "ordinal": int(row.get("ordinal") or 0),
            "alive": _is_alive(row),
        }

    # 只撤销**目标版本之后**的变更；降序：晚发生的先撤。
    # 由本函数自己过滤而不是要求调用方预先筛好 —— 传进来的是全量时不至于静默算错。
    ordered = sorted(history, key=lambda item: int(item.get("version_no") or 0), reverse=True)
    for version in ordered:
        if int(version.get("version_no") or 0) <= target_version:
            continue
        changes = version.get("feature_changes") or []
        # 同一版本内也逆序：一份 feature_changes 是顺序施加的，逆操作就该逆序。
        for change in reversed(list(changes)):
            if not isinstance(change, Mapping):
                continue
            key = _feature_key_of(change.get("feature_key"))
            if not key:
                continue
            # 防御：记录指向表里没有的 key（软删时代不该发生）。不抛异常，
            # 按记录本身复原，ordinal 取 0。
            entry = state.setdefault(
                key,
                {"content": "", "module_key": None, "module_name": None, "ordinal": 0, "alive": False},
            )
            op = str(change.get("op") or "").strip()
            if op == "add":
                entry["alive"] = False
            elif op == "modify":
                before = change.get("before")
                if before is not None:
                    entry["content"] = str(before)
                module_before = change.get("module_before")
                if module_before is not None:
                    entry["module_key"] = _normalize_module(module_before)
                    # name 未随记录保存，按全仓约定退化为 key。
                    entry["module_name"] = entry["module_key"]
            elif op == "delete":
                entry["alive"] = True
                if change.get("content") is not None:
                    entry["content"] = str(change["content"])
            # 其余 op（未知/未来新增）忽略：撤不掉的总比撤错了好。

    rows = [(key, item) for key, item in state.items() if item["alive"]]
    rows.sort(key=lambda pair: (pair[1]["ordinal"], pair[0]))
    return [
        FeatureState(
            feature_key=key,
            content=item["content"],
            ordinal=item["ordinal"],
            module_key=item["module_key"],
            module_name=item["module_name"],
        )
        for key, item in rows
    ]


def plan_revert(
    current_rows: Sequence[Mapping[str, Any]],
    target_states: Sequence[FeatureState],
) -> list[PlannedRevertRow]:
    """当前行 vs 目标时刻行 → 逐条归宿（按 `feature_key` 对齐）。

    `current_rows` 同样要含已软删行：命中已软删行的 `add` 会被标成 `revived=True`，
    写库侧据此走 UPDATE 复活而不是 INSERT 新行。
    """
    by_key: dict[str, Mapping[str, Any]] = {}
    for row in current_rows:
        key = _feature_key_of(row.get("feature_key"))
        if key:
            by_key[key] = row

    alive_keys = {key for key, row in by_key.items() if _is_alive(row)}
    planned: list[PlannedRevertRow] = []
    seen: set[str] = set()

    for item in target_states:
        seen.add(item.feature_key)
        row = by_key.get(item.feature_key)
        row_id = int(row["id"]) if row is not None and row.get("id") is not None else None
        if row is None:
            # 表里没有这个 key：只能新建（防御路径，防御理由同 reconstruct）。
            planned.append(
                PlannedRevertRow(
                    op="add",
                    feature_key=item.feature_key,
                    content=item.content,
                    ordinal=item.ordinal,
                    module_key=item.module_key,
                    module_name=item.module_name,
                    row_id=None,
                )
            )
            continue
        if item.feature_key not in alive_keys:
            # 当前已软删、目标时刻还在 → 复活原行（保住 feature_key 与能力关联）。
            planned.append(
                PlannedRevertRow(
                    op="add",
                    feature_key=item.feature_key,
                    content=item.content,
                    ordinal=item.ordinal,
                    module_key=item.module_key,
                    module_name=item.module_name,
                    row_id=row_id,
                    revived=True,
                )
            )
            continue

        current_content = str(row.get("content") or "")
        changed = current_content != item.content
        current_module = (
            _normalize_module(row.get("module_key")),
            _normalize_module(row.get("module_name")),
        )
        module_before = current_module if (item.module_key, item.module_name) != current_module else None
        planned.append(
            PlannedRevertRow(
                op="modify" if changed else "keep",
                feature_key=item.feature_key,
                content=item.content,
                ordinal=item.ordinal,
                module_key=item.module_key,
                module_name=item.module_name,
                row_id=row_id,
                before=current_content if changed else None,
                module_before=module_before,
            )
        )

    # 当前生效、但目标时刻不存在的 → 软删。
    for key, row in by_key.items():
        if key in seen or key not in alive_keys:
            continue
        planned.append(
            PlannedRevertRow(
                op="delete",
                feature_key=key,
                content=str(row.get("content") or ""),
                ordinal=int(row.get("ordinal") or 0),
                module_key=_normalize_module(row.get("module_key")),
                module_name=_normalize_module(row.get("module_name")),
                row_id=int(row["id"]) if row.get("id") is not None else None,
                before=str(row.get("content") or ""),
            )
        )
    return planned
