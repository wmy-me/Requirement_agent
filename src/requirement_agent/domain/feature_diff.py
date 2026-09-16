"""功能行同步的 diff 内核（纯函数：不接 session、不写库、不读配置）。

**为什么要有这个模块。** `RequirementFeatureRepository.sync_features` 原先用
**ordinal 位置当身份** —— 新来源的第 N 行永远去配对现有 `ordinal = N` 的 feature。
位置不是身份：往中间插入一行，它后面的每一行都会被判成 `modify` 并**真实覆写**
`content` / `content_hash` / `provenance`；而删除的判据是「位置超出末尾」而不是
「内容未命中」，所以 3 行的来源并进 10 行的目标，会把目标删剩 3 行。被污染的
`content_hash` 还会让后续的精确匹配也失效，误判逐轮累积。

把匹配抽成纯函数有两个收益：一是匹配规则可被直接单测（不必建库），二是**写库路径与
预览路径共用同一份算法** —— 预览界面才敢说「所见即所落」。

**两条容易搞错、已写死的规则：**

1. **模块标签的合并不是对称的。** 「新行没有模块」**不等于**「要清空模块」——缺信息不是
   信息。若按对称处理，把一条不带模块的扁平来源合进 `REQ-000015`（12 条带模块的功能），
   会把 12 条模块标签全部抹掉，是纯粹的破坏。
2. **匹配上的行保持原 ordinal，新行追加到末尾。** 不做「按 incoming 顺序整体重编」——
   那样每次合并都会把目标 REQ 的既有功能全部重排。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["FeatureRow", "PlannedRow", "normalize_feature_rows", "plan_sync"]

Op = Literal["keep", "modify", "add", "delete"]


def _content_hash(content: str) -> str:
    """与 `create_features` / `sync_features` 落库时同一口径（sha256 十六进制）。

    刻意**现算而不读 `requirement_feature.content_hash` 列**：该列在 `005` 里可空且从未
    回填，历史行可能是 NULL，信它会让匹配静默失灵。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_module(value: Any) -> str | None:
    """模块标签归一：缺失 / 空串 / 纯空白一律视为「无模块」。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """一条来源功能行。`module_key` 为 None 表示它不归属任何模块。"""

    content: str
    module_key: str | None = None
    module_name: str | None = None

    @property
    def content_hash(self) -> str:
        return _content_hash(self.content)


@dataclass(frozen=True, slots=True)
class PlannedRow:
    """一条现有 feature（或待新增行）在一次同步里的归宿。

    `op` 描述的是**内容**层面的关系，与既有 `feature_changes` 的三元组契约一致
    （`add` / `modify` / `delete`，`keep` 不产出变更记录）。模块标签的迁移由
    `module_before` / `module_after` 单独表达：模块变了但内容没变时 `op` 仍是 `keep`，
    避免往版本记录里写一条 `before == after` 的退化 modify。
    """

    op: Op
    ordinal: int
    content: str
    module_key: str | None
    module_name: str | None
    feature_key: str | None = None
    feature_id: int | None = None
    before: str | None = None
    module_before: tuple[str | None, str | None] | None = None
    match_kind: str | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """现有 active feature 的匹配用视图。"""

    index: int
    feature_id: int | None
    feature_key: str | None
    content: str
    module_key: str | None
    module_name: str | None
    ordinal: int

    @property
    def content_hash(self) -> str:
        return _content_hash(self.content)


def normalize_feature_rows(raw: Sequence[str | Mapping[str, Any]]) -> list[FeatureRow]:
    """把来源功能行归一成 `FeatureRow`，是「字符串行 / dict 行」的唯一入口。

    接受 `str`（视为不带模块）与 `{"content", "module_key", "module_name"}` 的 dict；
    逐条 strip、丢弃空行、保持原顺序。`module_name` 缺失时回落到 `module_key`
    （与 `commit_nodes._module_lines` 产出的行形状一致）。
    """
    rows: list[FeatureRow] = []
    for item in raw:
        if isinstance(item, Mapping):
            content = str(item.get("content") or "").strip()
            module_key = _normalize_module(item.get("module_key"))
            module_name = _normalize_module(item.get("module_name")) or module_key
        else:
            content = str(item).strip()
            module_key = module_name = None
        if not content:
            continue
        rows.append(FeatureRow(content=content, module_key=module_key, module_name=module_name))
    return rows


def plan_sync(
    existing: Sequence[Mapping[str, Any]],
    incoming: Sequence[FeatureRow],
    *,
    prune: bool = False,
) -> list[PlannedRow]:
    """现有 active features vs 新来源功能行 → 逐条归宿。

    匹配分三遍，前三遍都取「ordinal 离期望位置最近」的未占用行：

    1. **模块组内**按 `content_hash` 精确匹配 —— 正常情形。
    2. 仍未匹配的行**跨模块**按 `content_hash` 匹配 —— 处理「模块改名」（「登录」→
       「用户登录」）。没有这一遍，改个模块名就会退化成 delete + add：
       `feature_key` 断裂、provenance 断链、版本 diff 显示成「删一条加一条」。
    3. 仍剩下的行在**同模块内按序配对**成 `modify` —— 处理「内容被改写」。
       没有这一遍，措辞一改哈希就对不上，只能落成「旧的留着 + 新的加一条」，
       并集语义下目标 REQ 会积起近似重复行。

    第 3 遍用的是位置启发式（与旧实现同源），但**不会级联**：内容没变的行已经在
    第 1/2 遍被摘走，剩下的只可能是真正改过或全新的行。

    `prune=False`（默认，合并用）时未命中的现有行**保留**，合并语义是并集；
    `prune=True`（回滚用）时未命中的现有行软删，语义是以来源为准整体替换。
    同一个内核喂这两个场景，正是把它抽成纯函数的收益。
    """
    candidates = [
        _Candidate(
            index=index,
            feature_id=int(row["id"]) if row.get("id") is not None else None,
            feature_key=str(row["feature_key"]) if row.get("feature_key") else None,
            content=str(row.get("content") or "").strip(),
            module_key=_normalize_module(row.get("module_key")),
            module_name=_normalize_module(row.get("module_name")),
            ordinal=int(row.get("ordinal") or 0),
        )
        for index, row in enumerate(existing)
    ]

    used: set[int] = set()
    matched: dict[int, tuple[_Candidate, str]] = {}

    def _find(incoming_index: int, *, require_module: bool) -> _Candidate | None:
        row = incoming[incoming_index]
        want = incoming_index + 1
        best: _Candidate | None = None
        best_key: tuple[int, int] | None = None
        for candidate in candidates:
            if candidate.index in used or candidate.content_hash != row.content_hash:
                continue
            if require_module and candidate.module_key != row.module_key:
                continue
            key = (abs(candidate.ordinal - want), candidate.ordinal)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        return best

    # 第 1 遍（同模块）/ 第 2 遍（跨模块）：按内容哈希精确匹配。
    for require_module in (True, False):
        for incoming_index in range(len(incoming)):
            if incoming_index in matched:
                continue
            candidate = _find(incoming_index, require_module=require_module)
            if candidate is not None:
                used.add(candidate.index)
                matched[incoming_index] = (candidate, "module" if require_module else "content")

    # 第 3 遍：把剩下的「内容变了」的行在同模块内按序配对，记为 modify。
    # 没有这一遍，任何措辞调整都会变成「旧的留着 + 新的加一条」——并集语义下目标 REQ
    # 会慢慢积起一堆近似重复的功能；改成 delete + add 又会让 feature_key 断裂、
    # provenance 断链。**这一遍不会像旧实现那样级联**：哈希相同的行已经在第 1/2 遍
    # 被摘走，剩下的只可能是真正改过内容或全新的行。
    pending: dict[str | None, list[_Candidate]] = {}
    for candidate in candidates:
        if candidate.index not in used:
            pending.setdefault(candidate.module_key, []).append(candidate)
    for group in pending.values():
        group.sort(key=lambda item: item.ordinal)
    cursor: dict[str | None, int] = {}
    for incoming_index, row in enumerate(incoming):
        if incoming_index in matched:
            continue
        group = pending.get(row.module_key) or []
        offset = cursor.get(row.module_key, 0)
        if offset >= len(group):
            continue
        cursor[row.module_key] = offset + 1
        candidate = group[offset]
        used.add(candidate.index)
        matched[incoming_index] = (candidate, "paired")

    # 未匹配的现有行：prune 时软删，否则保留。两者的 ordinal 都保持原值。
    leftovers = [c for c in candidates if c.index not in used]

    # 新增行追加到现有最大序号之后 —— 不动既有行的版式。
    next_ordinal = max((c.ordinal for c in candidates), default=0) + 1

    planned: list[PlannedRow] = []
    for incoming_index, row in enumerate(incoming):
        entry = matched.get(incoming_index)
        if entry is None:
            planned.append(
                PlannedRow(
                    op="add",
                    ordinal=next_ordinal,
                    content=row.content,
                    module_key=row.module_key,
                    module_name=row.module_name,
                )
            )
            next_ordinal += 1
            continue

        candidate, match_kind = entry
        # 模块标签：新行没给模块就保留原有的（缺信息不是信息）。
        if row.module_key is None:
            effective_key, effective_name = candidate.module_key, candidate.module_name
        else:
            effective_key = row.module_key
            effective_name = row.module_name or row.module_key
        module_before = None
        if (effective_key, effective_name) != (candidate.module_key, candidate.module_name):
            module_before = (candidate.module_key, candidate.module_name)

        content_changed = row.content != candidate.content
        planned.append(
            PlannedRow(
                op="modify" if content_changed else "keep",
                ordinal=candidate.ordinal,
                content=row.content,
                module_key=effective_key,
                module_name=effective_name,
                feature_key=candidate.feature_key,
                feature_id=candidate.feature_id,
                before=candidate.content if content_changed else None,
                module_before=module_before,
                match_kind=match_kind,
            )
        )

    for candidate in leftovers:
        planned.append(
            PlannedRow(
                op="delete" if prune else "keep",
                ordinal=candidate.ordinal,
                content=candidate.content,
                module_key=candidate.module_key,
                module_name=candidate.module_name,
                feature_key=candidate.feature_key,
                feature_id=candidate.feature_id,
                before=candidate.content,
            )
        )
    return planned
