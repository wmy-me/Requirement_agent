"""文档分片的同步计划 —— **直接复用功能条目的 diff 内核**。

分片（`document_chunk`）与功能条目（`requirement_feature`）是同一个问题：
一批文本行，要算出哪些没变、哪些改了、哪些新增、哪些删除。

E 批已经在功能条目上把这个问题解决过一遍（内容哈希匹配 + 保序 + 非级联，
内核在 `feature_diff.plan_sync`），**这里不重写第二份** —— 两份实现迟早会分叉，
而分叉后的行为差异极难定位（「为什么功能删除判得对、分片就判错」）。

本模块只做**映射**：把分片的形状（`chunk_index` / `chunk_text`）翻译成内核认识的
行形状，再把结果翻回去。

与功能条目的唯一实质差异是 `prune` 的默认值：

| | 默认 | 为什么 |
|---|---|---|
| 功能条目（合并需求） | `prune=False`（并集） | 「来源没提到」不等于「要删掉」 |
| 分片（文档更新） | `prune=True`（以新内容为准） | 文档改了就是改了，旧分片该删就删 |
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from requirement_agent.domain.feature_diff import PlannedRow, normalize_feature_rows, plan_sync

__all__ = ["plan_chunk_sync"]


def plan_chunk_sync(
    existing_chunks: Sequence[Mapping[str, object]],
    incoming_texts: Sequence[str],
    *,
    prune: bool = True,
) -> list[PlannedRow]:
    """算出「新内容相对现有分片，每个分片的归宿」。

    `existing_chunks` 元素需含 `id` / `chunk_index` / `chunk_text`
    （即 `DocumentAssetRepository.list_chunks` 的输出形状）。

    返回的 `PlannedRow.ordinal` 即**新的 chunk_index**（沿用调用方给的进制，
    不做 ±1 偏移）；`feature_key` 里放的是**旧的 chunk_index** —— 内核里那个字段
    叫 feature_key，这里只是借它的形状装序号。

    ⚠️ **不要给 ordinal 偏移**。内核的「新增追加到末尾」是按 `max(ordinal) + 1`
    算的，多偏移一位会让新分片序号跳号（实测踩过）。
    """
    existing_rows = [
        {
            "id": chunk.get("id"),
            # 借内核的 feature_key 字段装旧序号 —— 调用方靠它知道「这条是原来哪一片」
            "feature_key": str(chunk.get("chunk_index")),
            "content": str(chunk.get("chunk_text") or ""),
            "ordinal": int(chunk.get("chunk_index") or 0),  # 不偏移：沿用 chunk_index 的进制
        }
        for chunk in existing_chunks
    ]
    return plan_sync(
        existing_rows,
        normalize_feature_rows(list(incoming_texts)),
        prune=prune,
    )
